# pyright: reportPrivateUsage=false
import json
from typing import Any, cast

import pytest

from nyx.activity.exploration import (
    _MAX_STEPS,
    Exploration,
    ExplorationState,
    should_explore,
)
from nyx.config import ExplorationConfig
from nyx.eval.evaluator import Evaluator
from nyx.llm.client import LlmClient, LlmMessage
from nyx.tools.registry import ToolRegistry
from nyx.types import LLMOutput

_PLAN_JSON = json.dumps({"focus": "骑士团", "done": False})


class _FakeLlm:
    def __init__(self, content: str = _PLAN_JSON) -> None:
        self.calls: list[str] = []
        self.correlation_ids: list[str] = []
        self._content = content

    async def complete(
        self,
        messages: list[LlmMessage],
        *,
        module: str,
        output_type: str,
        correlation_id: str,
        json_mode: bool = False,
    ) -> LLMOutput:
        self.calls.append(output_type)
        self.correlation_ids.append(correlation_id)
        return LLMOutput(
            id=f"llm-{len(self.calls)}",
            module=module,
            type=output_type,
            model="fake",
            content=self._content,
            token_usage={"input": 1, "output": 1},
            correlation_id=correlation_id,
        )


class _FakeEvaluator:
    def __init__(self) -> None:
        self.evaluated: list[LLMOutput] = []

    async def evaluate(self, output: LLMOutput) -> None:
        self.evaluated.append(output)


class _FakeTools:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def call(self, name: str, args: dict[str, Any]) -> Any:
        self.calls.append((name, args))
        if name in ("local_search", "web_search"):
            return ["一条检索结果"]
        return "文件内容"


class _WebTools:
    """web_search 返回带 url 的结果，web_fetch 记录调用（可配置抛错）。"""

    def __init__(self, fetch_raises: bool = False) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self._fetch_raises = fetch_raises

    async def call(self, name: str, args: dict[str, Any]) -> Any:
        self.calls.append((name, args))
        if name == "web_search":
            return [{"url": "https://example.com/a"}]
        if name == "web_fetch":
            if self._fetch_raises:
                raise RuntimeError("download fail")
            return {"path": "/x", "filename": "a.txt", "total_chars": 3}
        return "其他"


def _make_exploration(
    llm: _FakeLlm,
    evaluator: _FakeEvaluator,
    tools: _FakeTools,
    web_enabled: bool = False,
) -> Exploration:
    return Exploration(
        cast(LlmClient, llm),
        cast(Evaluator, evaluator),
        cast(ToolRegistry, tools),
        ExplorationConfig(web_enabled=web_enabled),
    )


# ---- should_explore ----


def test_should_explore_energy_too_low() -> None:
    assert should_explore(59.0, 0.0, 4, 100_000.0) is False


def test_should_explore_rate_limited() -> None:
    assert should_explore(60.0, 1_000.0, 4, 15_399.0) is False


def test_should_explore_ok() -> None:
    assert should_explore(60.0, 0.0, 4, 20_000.0) is True


# ---- Exploration.run ----


async def test_exploration_run_no_web() -> None:
    llm = _FakeLlm()
    evaluator = _FakeEvaluator()
    tools = _FakeTools()
    expl = _make_exploration(llm, evaluator, tools)
    result = await expl.run("骑士团", "corr-1")
    assert set(result) == {"findings", "notes"}
    assert llm.calls == ["exploration_plan"] * _MAX_STEPS
    assert llm.correlation_ids == ["corr-1"] * _MAX_STEPS
    assert len(evaluator.evaluated) == _MAX_STEPS
    assert all(c[0] != "web_search" for c in tools.calls)


async def test_exploration_run_web() -> None:
    llm = _FakeLlm()
    evaluator = _FakeEvaluator()
    tools = _FakeTools()
    expl = _make_exploration(llm, evaluator, tools, web_enabled=True)
    result = await expl.run("骑士团", "corr-1")
    assert set(result) == {"findings", "notes"}
    assert any(c[0] == "web_search" for c in tools.calls)


async def test_exploration_plan_non_dict_raises() -> None:
    llm = _FakeLlm(content=json.dumps([1, 2, 3]))
    expl = _make_exploration(llm, _FakeEvaluator(), _FakeTools())
    with pytest.raises(ValueError):
        await expl.run("骑士团", "corr-1")


# ---- _search_web：主动下载资料 ----


def _web_exploration(tools: _WebTools) -> Exploration:
    return Exploration(
        cast(LlmClient, _FakeLlm()),
        cast(Evaluator, _FakeEvaluator()),
        cast(ToolRegistry, tools),
        ExplorationConfig(web_enabled=True),
    )


def _web_state() -> ExplorationState:
    return {
        "seed": "x", "focus": "骑士", "findings": [], "notes": [],
        "step": 0, "done": False, "correlation_id": "c",
    }


async def test_search_web_downloads_first_result() -> None:
    tools = _WebTools()
    expl = _web_exploration(tools)
    state = _web_state()
    await expl._search_web(state)
    assert ("web_fetch", {"url": "https://example.com/a"}) in tools.calls
    assert any("已下载资料" in f for f in state["findings"])


async def test_search_web_no_crash_when_download_fails() -> None:
    tools = _WebTools(fetch_raises=True)
    expl = _web_exploration(tools)
    state = _web_state()
    await expl._search_web(state)  # 下载失败静默吞掉，不崩
    assert len(state["findings"]) == 1  # 只剩 web_search 结果串，无「已下载资料」
