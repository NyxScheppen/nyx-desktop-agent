# pyright: reportPrivateUsage=false
import json
from typing import Any, cast

import pytest

from nyx.activity.exploration import (
    _KIND_DEAD_END,
    _KIND_REAL,
    _KIND_SAFE_ROOM,
    Exploration,
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


def _web_exploration(tools: _WebTools) -> Exploration:
    return Exploration(
        cast(LlmClient, _FakeLlm()),
        cast(Evaluator, _FakeEvaluator()),
        cast(ToolRegistry, tools),
        cast(EventBus, _FakeBus()),
        ExplorationConfig(web_enabled=True),
    )


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


# ---- 逐层 run 机制：interrupt / resume ----

async def test_start_interrupts_at_first_decision() -> None:
    expl = _web_exploration(_WebTools())
    p = await expl.start(None, "量子", 100.0, "a1", "c1")
    assert p["pending"] is True
    assert p["decision"]["kind"] == "choose"
    assert len(p["decision"]["nodes"]) == 3


async def test_resume_choice_visits_node_and_interrupts_again() -> None:
    expl = _web_exploration(_WebTools())
    await expl.start(None, "量子", 100.0, "a1", "c1")
    p = await expl.resume("a1", "node:0")
    assert p["pending"] is True
    # 进了一个真实节点后精力下降（100 - 6 = 94）
    assert p["state"]["energy"] == pytest.approx(94.0)


async def test_resume_retreat_finalizes() -> None:
    expl = _web_exploration(_WebTools())
    await expl.start(None, "量子", 100.0, "a1", "c1")
    p = await expl.resume("a1", "retreat")
    assert p["pending"] is False
    assert p["result"]["type"] == "free_exploration"
    assert p["result"]["outcome"] == "retreated"


# ---- 终局 LLM 判定（Task 4） ----


_FINALIZE_JSON = json.dumps({
    "summary": "弄懂了量子退相干的机制",
    "core_discovery": "退相干是量子系统与环境纠缠导致的表观坍缩",
    "knowledge": [{"topic": "退相干", "content": "环境纠缠抹去相干性"}],
    "strong_new_topics": ["量子纠错"],
    "casual_new_topics": ["退火算法"],
})


async def test_finalize_judges_won() -> None:
    expl = Exploration(
        cast(LlmClient, _FakeLlm(_FINALIZE_JSON)),
        cast(Evaluator, _FakeEvaluator()),
        cast(ToolRegistry, _WebTools()),
        cast(EventBus, _FakeBus()),
        ExplorationConfig(web_enabled=True),
    )
    await expl.start(None, "量子", 100.0, "a1", "c1")
    p = await expl.resume("a1", "retreat")
    # retreat 触发终局判定，但核心发现命中 → won 覆盖 retreat
    assert p["result"]["outcome"] == "won"
    assert p["result"]["core_discovery"] != ""
    assert p["result"]["knowledge"][0]["topic"] == "退相干"
