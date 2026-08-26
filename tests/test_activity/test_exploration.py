# pyright: reportPrivateUsage=false
import json
from typing import Any, cast

import pytest

from nyx.activity.exploration import (
    _KIND_DEAD_END,
    _KIND_REAL,
    _KIND_SAFE_ROOM,
    _MAX_STEPS,
    Exploration,
    ExplorationState,
    FloorNode,
    descent_cost,
    determine_outcome,
    enter_cost,
    fill_dead_ends,
    parse_choice,
    restore_energy,
    should_explore,
)
from nyx.config import ExplorationConfig
from nyx.enums import EventType
from nyx.eval.evaluator import Evaluator
from nyx.events.bus import EventBus
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


class _EmptyWebTools(_WebTools):
    """web_search 返回空 → 触发本地兜底（local_search 返回非 dict 的 str 结果）。"""

    async def call(self, name: str, args: dict[str, Any]) -> Any:
        self.calls.append((name, args))
        if name == "web_search":
            return []
        if name == "local_search":
            return ["本地兜底结果"]
        return "其他"


class _FakeBus:
    def __init__(self) -> None:
        self.published: list[Any] = []

    async def publish(self, event: Any) -> None:
        self.published.append(event)


def _make_exploration(
    llm: _FakeLlm,
    evaluator: _FakeEvaluator,
    tools: _FakeTools,
    bus: _FakeBus | None = None,
    web_enabled: bool = False,
) -> Exploration:
    return Exploration(
        cast(LlmClient, llm),
        cast(Evaluator, evaluator),
        cast(ToolRegistry, tools),
        cast(EventBus, bus if bus is not None else _FakeBus()),
        ExplorationConfig(web_enabled=web_enabled),
    )


# ---- should_explore ----


def test_should_explore_rate_limited() -> None:
    # 频率未过（now - last < 1h*3600）→ False，与精力无关
    assert should_explore(1_000.0, 1, 1_000.0 + 3_599.0) is False


def test_should_explore_ok() -> None:
    # last=0（从未探索）+ 频率已过 → True；无 energy 入参（精力交 build_schedule 兜底）
    assert should_explore(0.0, 1, 20_000.0) is True


# ---- Exploration.run ----


async def test_exploration_run_no_web() -> None:
    llm = _FakeLlm()
    evaluator = _FakeEvaluator()
    tools = _FakeTools()
    expl = _make_exploration(llm, evaluator, tools)
    result = await expl.run("骑士团", "a1", "corr-1")
    assert set(result) == {"findings", "notes", "nodes"}
    assert llm.calls == ["exploration_plan"] * _MAX_STEPS
    assert llm.correlation_ids == ["corr-1"] * _MAX_STEPS
    assert len(evaluator.evaluated) == _MAX_STEPS
    assert all(c[0] != "web_search" for c in tools.calls)


async def test_exploration_run_web() -> None:
    llm = _FakeLlm()
    evaluator = _FakeEvaluator()
    tools = _FakeTools()
    expl = _make_exploration(llm, evaluator, tools, web_enabled=True)
    result = await expl.run("骑士团", "a1", "corr-1")
    assert set(result) == {"findings", "notes", "nodes"}
    assert any(c[0] == "web_search" for c in tools.calls)


async def test_exploration_plan_non_dict_raises() -> None:
    llm = _FakeLlm(content=json.dumps([1, 2, 3]))
    expl = _make_exploration(llm, _FakeEvaluator(), _FakeTools())
    with pytest.raises(ValueError):
        await expl.run("骑士团", "a1", "corr-1")


async def test_exploration_run_returns_nodes_and_publishes_steps() -> None:
    llm = _FakeLlm()
    evaluator = _FakeEvaluator()
    tools = _FakeTools()
    bus = _FakeBus()
    expl = _make_exploration(llm, evaluator, tools, bus=bus)
    result = await expl.run("骑士团", "a1", "corr-1")
    # nodes 非空；search 节点在前；每节点对应一条 EXPLORATION_STEP
    assert result["nodes"]
    assert all(n["kind"] in ("search", "web") for n in result["nodes"])
    steps = [e for e in bus.published if e.type is EventType.EXPLORATION_STEP]
    assert len(steps) == len(result["nodes"])
    assert steps[0].content["activity_id"] == "a1"
    assert steps[0].content["node"] == result["nodes"][0]


class _CrashOnReadTools(_FakeTools):
    """file_io 的 read 动作抛 FileNotFoundError，复现旧 bug（主题被当文件路径读）。"""

    async def call(self, name: str, args: dict[str, Any]) -> Any:
        self.calls.append((name, args))
        if name == "file_io" and args.get("action") == "read":
            raise FileNotFoundError(str(args.get("path")))
        if name in ("local_search", "web_search"):
            return ["一条检索结果"]
        return "文件内容"


async def test_exploration_never_reads_focus_as_file() -> None:
    # 回归：read 死节点曾把探索主题当文件路径 read → FileNotFoundError 崩整个活动。
    # 移除后链上只剩搜索 + 写笔记，file_io 仅 write 不 read。
    tools = _CrashOnReadTools()
    expl = _make_exploration(_FakeLlm(), _FakeEvaluator(), tools, web_enabled=True)
    result = await expl.run("纽约尼克斯队2024-2025赛季的战术变化", "a1", "corr-1")
    assert set(result) == {"findings", "notes", "nodes"}
    assert all(
        c[0] != "file_io" or c[1].get("action") != "read" for c in tools.calls
    )


# ---- Exploration.pick_topic ----


async def test_pick_topic_returns_topic() -> None:
    llm = _FakeLlm(content=json.dumps({"topic": "深海鱼"}))
    expl = _make_exploration(llm, _FakeEvaluator(), _FakeTools())
    assert await expl.pick_topic("corr-1") == "深海鱼"
    assert llm.calls == ["exploration_topic"]


async def test_pick_topic_non_dict_raises() -> None:
    llm = _FakeLlm(content=json.dumps([1, 2, 3]))
    expl = _make_exploration(llm, _FakeEvaluator(), _FakeTools())
    with pytest.raises(ValueError):
        await expl.pick_topic("corr-1")


async def test_pick_topic_fallback() -> None:
    llm = _FakeLlm(content=json.dumps({"other": "x"}))
    expl = _make_exploration(llm, _FakeEvaluator(), _FakeTools())
    assert await expl.pick_topic("corr-1") == "有趣的新鲜事"


# ---- _search_web：主动下载资料 ----


def _web_exploration(tools: _WebTools) -> Exploration:
    return Exploration(
        cast(LlmClient, _FakeLlm()),
        cast(Evaluator, _FakeEvaluator()),
        cast(ToolRegistry, tools),
        cast(EventBus, _FakeBus()),
        ExplorationConfig(web_enabled=True),
    )


def _web_state() -> ExplorationState:
    return {
        "seed": "x", "focus": "骑士", "findings": [], "notes": [],
        "nodes": [], "step": 0, "done": False,
        "activity_id": "a1", "correlation_id": "c",
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


async def test_search_web_falls_back_to_local() -> None:
    tools = _EmptyWebTools()
    expl = _web_exploration(tools)
    state = _web_state()
    await expl._search_web(state)
    assert ("local_search", {"query": "骑士"}) in tools.calls
    assert any("本地兜底结果" in f for f in state["findings"])


# ---- 逐层地牢：纯函数 ----

def _node(kind: str, name: str = "节点") -> FloorNode:
    return {
        "name": name,
        "url": "",
        "kind": kind,
        "snippet": "",
        "may_encounter": False,
    }


def test_fill_dead_ends_pads_to_target():
    filled = fill_dead_ends([_node(_KIND_REAL)])
    assert len(filled) == 3
    assert filled[0]["kind"] == _KIND_REAL
    assert filled[1]["kind"] == _KIND_DEAD_END


def test_enter_cost_by_kind():
    assert enter_cost(_node(_KIND_REAL)) == 6.0
    assert enter_cost(_node(_KIND_DEAD_END)) == 4.0
    assert enter_cost(_node(_KIND_SAFE_ROOM)) == 0.0


def test_descent_cost_increases_with_floor():
    assert descent_cost(1) < descent_cost(3)


def test_restore_energy_caps_at_max():
    assert restore_energy(80.0) == 100.0
    assert restore_energy(40.0) == 70.0


def test_determine_outcome_three_ways():
    assert determine_outcome(50.0, "真相", False) == "won"
    assert determine_outcome(0.0, "", False) == "exhausted"
    assert determine_outcome(50.0, "", True) == "retreated"


def test_parse_choice_routes():
    state = {"current_nodes": [_node(_KIND_REAL)]}
    assert parse_choice("node:0", state) == ("visit", 0)
    assert parse_choice("safe_room", state) == ("safe_room", None)
    assert parse_choice("retreat", state) == ("retreat", None)
    assert parse_choice("node:9", state) == ("retreat", None)


# ---- 逐层地牢：_search_nodes 本层真实搜索 ----

async def test_search_nodes_fills_real_results() -> None:
    expl = _web_exploration(_WebTools())
    nodes = await expl._search_nodes("量子", 1)
    assert nodes[0]["kind"] == _KIND_REAL
    assert nodes[0]["may_encounter"] is False


async def test_search_nodes_deep_floor_marks_encounter() -> None:
    expl = _web_exploration(_WebTools())
    nodes = await expl._search_nodes("量子", 3)
    assert nodes[0]["may_encounter"] is True
