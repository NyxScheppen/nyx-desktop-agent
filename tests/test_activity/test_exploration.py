# pyright: reportPrivateUsage=false
import json
from typing import Any, cast

from nyx.activity.exploration import _MAX_STEPS, Exploration, should_explore
from nyx.config import ExplorationConfig
from nyx.eval.evaluator import Evaluator
from nyx.llm.client import LlmClient, LlmMessage
from nyx.memory.facade import MemoryFacade
from nyx.tools.registry import ToolRegistry
from nyx.types import LLMOutput, Memory

_PLAN_JSON = json.dumps({"focus": "骑士团", "done": False})


class _FakeLlm:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.correlation_ids: list[str] = []

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
            content=_PLAN_JSON,
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


class _FakeMemory:
    def __init__(self) -> None:
        self.queries: list[str] = []

    async def search(self, query: str) -> list[Memory]:
        self.queries.append(query)
        return []


def _make_exploration(
    llm: _FakeLlm,
    evaluator: _FakeEvaluator,
    tools: _FakeTools,
    memory: _FakeMemory,
    web_enabled: bool = False,
) -> Exploration:
    return Exploration(
        cast(LlmClient, llm),
        cast(Evaluator, evaluator),
        cast(ToolRegistry, tools),
        cast(MemoryFacade, memory),
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
    memory = _FakeMemory()
    expl = _make_exploration(llm, evaluator, tools, memory)
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
    memory = _FakeMemory()
    expl = _make_exploration(llm, evaluator, tools, memory, web_enabled=True)
    result = await expl.run("骑士团", "corr-1")
    assert set(result) == {"findings", "notes"}
    assert any(c[0] == "web_search" for c in tools.calls)
