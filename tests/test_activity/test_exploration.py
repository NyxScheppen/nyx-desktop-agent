# pyright: reportPrivateUsage=false
import json
from collections.abc import Awaitable, Callable
from typing import Any, cast

from nyx.activity.exploration import Exploration, should_explore
from nyx.config import ExplorationConfig
from nyx.enums import MemoryType
from nyx.eval.evaluator import Evaluator
from nyx.llm.client import LlmClient, LlmMessage
from nyx.tools.registry import ToolRegistry
from nyx.types import LLMOutput, Memory

_FINALIZE_JSON = json.dumps({
    "summary": "弄懂了量子退相干的机制",
    "core_discovery": "退相干是量子系统与环境纠缠导致的表观坍缩",
    "knowledge": [{"topic": "退相干", "content": "环境纠缠抹去相干性"}],
    "strong_new_topics": ["量子纠错"],
    "casual_new_topics": ["退火算法"],
})


class _FakeLlm:
    def __init__(self, content: str = _FINALIZE_JSON) -> None:
        self._content = content
        self.calls: list[str] = []
        self.correlation_ids: list[str] = []
        self.user_contents: list[str] = []

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
        self.user_contents.append(messages[1]["content"])
        return LLMOutput(
            module=module,
            type=output_type,
            model="fake",
            content=self._content,
            correlation_id=correlation_id,
        )


class _FakeEvaluator:
    def __init__(self) -> None:
        self.evaluated: list[LLMOutput] = []

    async def evaluate(self, output: LLMOutput) -> None:
        self.evaluated.append(output)


class _FakeTools:
    def __init__(self, fetch_raises: bool = False) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self._fetch_raises = fetch_raises

    async def call(self, name: str, args: dict[str, Any]) -> Any:
        self.calls.append((name, args))
        if name == "local_search":
            # 真实 local_search 返回 {path, snippet}（无 title/url）
            return [{"path": "C:/notes/量子.txt", "snippet": "环境纠缠"}]
        if name == "web_search":
            return [{"title": "量子退相干", "url": "https://example.com/a",
                     "snippet": "环境纠缠"}]
        if name == "web_fetch":
            if self._fetch_raises:
                raise RuntimeError("download fail")
            # 真实 web_fetch 返回 {text, url}
            return {"text": "抓取的正文", "url": "https://example.com/a"}
        return "其他"


def _make_exploration(
    llm: _FakeLlm | None = None,
    tools: _FakeTools | None = None,
    web_enabled: bool = False,
    search_memories: Callable[[str], Awaitable[list[Memory]]] | None = None,
) -> Exploration:
    return Exploration(
        cast(LlmClient, llm if llm is not None else _FakeLlm()),
        cast(Evaluator, _FakeEvaluator()),
        cast(ToolRegistry, tools if tools is not None else _FakeTools()),
        ExplorationConfig(web_enabled=web_enabled),
        search_memories=search_memories,
    )


# ---- should_explore ----


def test_should_explore_rate_limited() -> None:
    # 频率未过（now - last < 1h*3600）→ False，与精力无关
    assert should_explore(1_000.0, 1, 1_000.0 + 3_599.0) is False


def test_should_explore_ok() -> None:
    # last=0（从未探索）+ 频率已过 → True；无 energy 入参（精力交 build_schedule 兜底）
    assert should_explore(0.0, 1, 20_000.0) is True


# ---- run：搜 → 抓正文 → 总结 ----


async def test_run_won_when_core_discovery() -> None:
    expl = _make_exploration(web_enabled=True)
    result = await expl.run("量子", "c1")
    assert result["type"] == "free_exploration"
    assert result["outcome"] == "won"
    assert result["core_discovery"] != ""
    assert result["knowledge"][0]["topic"] == "退相干"
    assert result["strong_new_topics"] == ["量子纠错"]


async def test_run_web_disabled_uses_local_search() -> None:
    tools = _FakeTools()
    expl = _make_exploration(tools=tools, web_enabled=False)
    await expl.run("量子", "c1")
    assert tools.calls[0][0] == "local_search"


async def test_run_local_search_results_flow_into_findings() -> None:
    # 回归：local_search 返回 {path, snippet}，name 取文件名、不再被 _result_parts 丢弃
    tools = _FakeTools()
    expl = _make_exploration(tools=tools, web_enabled=False)
    result = await expl.run("量子", "c1")
    assert len(result["findings"]) == 1
    assert "量子.txt" in result["findings"][0]
    assert "环境纠缠" in result["findings"][0]


async def test_run_web_enabled_uses_web_search() -> None:
    tools = _FakeTools()
    expl = _make_exploration(tools=tools, web_enabled=True)
    await expl.run("量子", "c1")
    assert tools.calls[0][0] == "web_search"


async def test_run_fetch_failure_falls_back_to_snippet() -> None:
    tools = _FakeTools(fetch_raises=True)
    expl = _make_exploration(tools=tools, web_enabled=True)
    result = await expl.run("量子", "c1")
    # web_fetch 抛错 → snippet 兜底，findings 仍有一条
    assert len(result["findings"]) == 1
    assert "环境纠缠" in result["findings"][0]


async def test_run_exhausted_when_no_core_discovery() -> None:
    llm = _FakeLlm(content=json.dumps({"summary": "没啥发现"}))
    expl = _make_exploration(llm=llm, web_enabled=True)
    result = await expl.run("量子", "c1")
    assert result["outcome"] == "exhausted"
    assert result["core_discovery"] == ""


async def test_run_llm_failure_returns_defaults() -> None:
    llm = _FakeLlm(content="不是 JSON")
    expl = _make_exploration(llm=llm, web_enabled=True)
    result = await expl.run("量子", "c1")
    assert result["outcome"] == "exhausted"
    assert result["knowledge"] == []
    assert result["strong_new_topics"] == []


async def test_summarize_injects_related_memories() -> None:
    async def search(_topic: str) -> list[Memory]:
        return [
            Memory(
                id="m1", created_at=1.0, content="旧认知", tag="explore",
                summary="之前想过退相干", freshness=1.0,
                type=MemoryType.SHORT_TERM,
            )
        ]

    llm = _FakeLlm()
    expl = _make_exploration(llm=llm, web_enabled=False, search_memories=search)
    await expl.run("量子", "c1")
    assert "之前想过退相干" in llm.user_contents[0]
