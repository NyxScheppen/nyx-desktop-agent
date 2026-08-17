# pyright: reportMissingTypeStubs=false, reportUnknownMemberType=false
# langgraph 类型标注松散：add_node/compile/ainvoke 返回部分未知、graph.state 缺 stub
import json
from collections.abc import Hashable
from typing import Any, TypedDict, cast

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from nyx.config import ExplorationConfig
from nyx.eval.evaluator import Evaluator
from nyx.events.event import SECONDS_PER_HOUR
from nyx.llm.client import LlmClient
from nyx.tools.registry import ToolRegistry

_MAX_STEPS = 8                    # 探索链最大步数（可推翻）
_FREE_EXPLORATION_ENERGY = 60.0   # 探索需精力 >= 此值（可推翻，design §8.6）


class ExplorationState(TypedDict):
    seed: str
    focus: str
    findings: list[str]
    notes: list[str]
    step: int
    done: bool
    correlation_id: str


def should_explore(
    energy: float, last_explored_at: float, rate_limit_hours: int, now: float
) -> bool:
    """自由探索升级门槛（纯函数）：精力充足 + 频率上限。

    「探索欲」条件由调用方结构保证：READING 活动仅由 DesireType.EXPLORATION 映射而来
    （13 desire_to_activity），故调用方在 activity.type is READING 时才调本函数。
    """
    if energy < _FREE_EXPLORATION_ENERGY:
        return False
    if now - last_explored_at < rate_limit_hours * SECONDS_PER_HOUR:
        return False
    return True


class Exploration:
    """跨域行为链（LangGraph）：好奇 → 搜索 → 读 → 写笔记（design §8.6）。"""

    def __init__(
        self,
        llm: LlmClient,
        evaluator: Evaluator,
        tools: ToolRegistry,
        exploration_config: ExplorationConfig,
    ) -> None:
        self._llm = llm
        self._evaluator = evaluator
        self._tools = tools
        self._web_enabled = exploration_config.web_enabled
        self._actions = ["search_local", "read", "write_note"]
        if self._web_enabled:
            self._actions.append("search_web")
        self._graph = self._build_graph()

    def _build_graph(self) -> CompiledStateGraph[ExplorationState]:
        g = StateGraph(ExplorationState)
        g.add_node("plan_next", self._plan_next)
        g.add_node("search_local", self._search_local)
        g.add_node("read", self._read)
        g.add_node("write_note", self._write_note)
        g.add_node("finalize", self._finalize)
        if self._web_enabled:
            g.add_node("search_web", self._search_web)
        g.add_edge(START, "plan_next")
        path_map: dict[Hashable, str] = {}
        for a in self._actions:
            path_map[a] = a
        path_map["finalize"] = "finalize"
        g.add_conditional_edges("plan_next", self._route, path_map)
        for a in self._actions:
            g.add_edge(a, "plan_next")
        g.add_edge("finalize", END)
        return g.compile()

    async def run(self, seed: str, correlation_id: str) -> dict[str, Any]:
        initial: ExplorationState = {
            "seed": seed, "focus": seed, "findings": [], "notes": [],
            "step": 0, "done": False, "correlation_id": correlation_id,
        }
        result = await self._graph.ainvoke(initial)
        return {"findings": result["findings"], "notes": result["notes"]}

    async def _plan_next(self, state: ExplorationState) -> ExplorationState:
        # MVP：LLM 判定 focus + done（json_mode）；step 达上限强制 done
        if state["step"] >= _MAX_STEPS:
            state["done"] = True
            return state
        output = await self._llm.complete(
            [
                {"role": "system", "content": _EXPLORATION_PLAN_SYSTEM},
                {
                    "role": "user",
                    "content": (
                        f"focus={state['focus']} findings={state['findings']} "
                        f"notes={state['notes']}"
                    ),
                },
            ],
            module="activity",
            output_type="exploration_plan",
            correlation_id=state["correlation_id"],
            json_mode=True,
        )
        await self._evaluator.evaluate(output)
        plan = json.loads(output.content)
        if not isinstance(plan, dict):
            raise ValueError(f"探索规划 JSON 应是对象，得到 {type(plan).__name__}")
        plan = cast(dict[str, Any], plan)
        state["focus"] = plan.get("focus", state["focus"])
        state["done"] = bool(plan.get("done", False))
        state["step"] = state["step"] + 1
        return state

    async def _search_local(self, state: ExplorationState) -> ExplorationState:
        res = await self._tools.call("local_search", {"query": state["focus"]})
        state["findings"].extend(str(r) for r in res)
        return state

    async def _search_web(self, state: ExplorationState) -> ExplorationState:
        res = await self._tools.call("web_search", {"query": state["focus"]})
        state["findings"].extend(str(r) for r in res)
        return state

    async def _read(self, state: ExplorationState) -> ExplorationState:
        res = await self._tools.call(
            "file_io", {"action": "read", "path": state["focus"]}
        )
        state["findings"].append(str(res))
        return state

    async def _write_note(self, state: ExplorationState) -> ExplorationState:
        note = "\n".join(state["findings"][-3:])
        await self._tools.call(
            "file_io",
            {"action": "write", "path": "exploration_note.md", "content": note},
        )
        state["notes"].append(note)
        return state

    async def _finalize(self, state: ExplorationState) -> ExplorationState:
        return state

    def _route(self, state: ExplorationState) -> str:
        if state["done"]:
            return "finalize"
        # MVP：确定性轮转（与 self._actions 对齐，含 search_web 时 4 步一轮），
        # 不靠 LLM 选具体动作
        # step 在 _plan_next 里先 +1，故 -1 对齐到 actions[0]=search_local 起始
        return self._actions[(state["step"] - 1) % len(self._actions)]


_EXPLORATION_PLAN_SYSTEM = (
    "你是尼克斯的探索规划器。按 JSON 输出 {focus, done}，"
    "决定下一步聚焦对象与是否结束。"
)
