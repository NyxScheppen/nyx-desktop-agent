# pyright: reportMissingTypeStubs=false, reportUnknownMemberType=false
# langgraph 类型标注松散：add_node/compile/ainvoke 返回部分未知、graph.state 缺 stub
import json
from typing import Any, TypedDict, cast
from urllib.parse import urlparse

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command, interrupt

from nyx.config import ExplorationConfig
from nyx.enums import EventType
from nyx.eval.evaluator import Evaluator
from nyx.events.bus import EventBus
from nyx.events.event import SECONDS_PER_HOUR, internal_event
from nyx.llm.client import LlmClient
from nyx.tools.registry import ToolRegistry

# ---- 逐层地牢常量（decision：先常量，不建配置项；调参需推翻 _MAX_STEPS 同例） ----
_NODE_SLOTS = 3            # 每层真实/死路节点槽数（安全房另算，不占槽）
_ENTER_NODE_COST = 6.0     # 进真实节点耗精力
_DEAD_END_COST = 4.0       # 进死路耗精力
_SAFE_ROOM_RESTORE = 30.0  # 安全房回精力
_MAX_ENERGY = 100.0        # 精力上限
_DESCENT_BASE_COST = 8.0   # 下楼基础消耗
_DESCENT_STEP_COST = 2.0   # 下楼每层递增消耗
_MAX_DEPTH = 5             # 深度上限（触底兜底判定核心发现）

_KIND_REAL = "real"
_KIND_DEAD_END = "dead_end"
_KIND_SAFE_ROOM = "safe_room"


class FloorNode(TypedDict):
    """一层的一个节点槽：真实结果 / 死路 / 安全房。"""
    name: str
    url: str
    kind: str          # _KIND_REAL | _KIND_DEAD_END | _KIND_SAFE_ROOM
    snippet: str
    may_encounter: bool  # 险节点：深楼层判定（floor >= 3），进后触发有根遭遇


def fill_dead_ends(
    nodes: list[FloorNode], target: int = _NODE_SLOTS
) -> list[FloorNode]:
    """槽位不足补死路：搜到几个真实结果填几个，不足 target 用死路槽补。纯函数。"""
    filled = list(nodes)
    while len(filled) < target:
        filled.append({
            "name": "本地搜索 · 无结果",
            "url": "",
            "kind": _KIND_DEAD_END,
            "snippet": "",
            "may_encounter": False,
        })
    return filled


def enter_cost(node: FloorNode) -> float:
    """进节点精力消耗：真实 6 / 死路 4 / 安全房 0。纯函数。"""
    if node["kind"] == _KIND_SAFE_ROOM:
        return 0.0
    if node["kind"] == _KIND_DEAD_END:
        return _DEAD_END_COST
    return _ENTER_NODE_COST


def descent_cost(floor: int) -> float:
    """从第 floor 层下楼到 floor+1 层的消耗，越深越贵。纯函数。"""
    return _DESCENT_BASE_COST + _DESCENT_STEP_COST * (floor - 1)


def restore_energy(energy: float) -> float:
    """安全房回精力，封顶 _MAX_ENERGY。纯函数。"""
    return min(_MAX_ENERGY, energy + _SAFE_ROOM_RESTORE)


def determine_outcome(energy: float, core_discovery: str, retreated: bool) -> str:
    """run 结局：won（挖到核心发现）/ exhausted（精力耗尽）/ retreated（主动撤退）。

    纯函数。
    """
    if core_discovery:
        return "won"
    if retreated:
        return "retreated"
    if energy <= 0.0:
        return "exhausted"
    return "retreated"  # 未赢未耗尽未主动退，兜底按撤退正常结算


def parse_choice(choice: str, state: dict[str, Any]) -> tuple[str, int | None]:
    """决策字符串 → (路由, 选中节点索引)；非法输入安全撤退。纯函数。

    路由取值：visit / safe_room / descend / retreat。
    """
    if choice == "safe_room":
        return "safe_room", None
    if choice == "descend":
        return "descend", None
    if choice == "retreat":
        return "retreat", None
    if choice.startswith("node:"):
        try:
            idx = int(choice.split(":", 1)[1])
        except ValueError:
            return "retreat", None
        nodes = state.get("current_nodes")
        if isinstance(nodes, list) and 0 <= idx < len(cast(list[Any], nodes)):
            return "visit", idx
    return "retreat", None


class ExplorationState(TypedDict):
    seed_desire_id: str | None
    seed_topic: str
    focus: str                       # 本层主题（初始=seed_topic，下楼时更新为线索）
    floor: int
    energy: float                    # run 内燃料（0-100）
    autopilot: bool
    current_nodes: list[FloorNode]   # 本层 3 槽（真实/死路；安全房另算）
    visited: list[dict[str, Any]]    # 已走过楼层/节点（前端地图）
    findings: list[str]
    knowledge: list[dict[str, str]]
    new_topics: list[str]            # 一般好奇（只留 run 记忆）
    strong_new_topics: list[str]     # 强烈新兴趣（→ add_long_term）
    encounters: list[dict[str, Any]]
    loot: list[dict[str, str]]
    npcs: list[dict[str, str]]
    summary: str
    core_discovery: str
    outcome: str
    retreated: bool
    _route: str                      # decide 解析出的路由（内部）
    _choice: int | None              # 选中节点索引（内部）
    _last_node: FloorNode | None     # 最近进入的节点（内部，facade 读险节点）
    activity_id: str
    correlation_id: str


class ExplorationProgress(TypedDict):
    pending: bool                  # True=还有决策点，False=已到终点
    decision: dict[str, Any]       # pending 时的决策载荷
    result: dict[str, Any]         # 非 pending 时的 run 结果（4.1 形状）
    state: dict[str, Any]          # 当前状态快照（facade 读 _last_node）


def assemble_result(state: ExplorationState) -> dict[str, Any]:
    """run 状态 → 4.1 结果形状（纯函数）。"""
    return {
        "type": "free_exploration",
        "seed": {"desire_id": state["seed_desire_id"], "topic": state["seed_topic"]},
        "outcome": state["outcome"],
        "floors_cleared": state["floor"] - 1,
        "summary": state["summary"],
        "core_discovery": state["core_discovery"],
        "knowledge": state["knowledge"],
        "new_topics": state["new_topics"],
        "encounters": state["encounters"],
        "loot": state["loot"],
        "npcs": state["npcs"],
    }


def should_explore(last_explored_at: float, rate_limit_hours: int, now: float) -> bool:
    """自由探索升级门槛（纯函数）：仅频率上限。

    「探索欲」条件由调用方结构保证：READING 活动仅由 DesireType.EXPLORATION 映射而来，
    故调用方在 activity.type is READING 时才调本函数。
    精力不再单独卡：探索消耗 -30，精力不足由 build_schedule 的 REST 穿插兜底。
    """
    return now - last_explored_at >= rate_limit_hours * SECONDS_PER_HOUR


class Exploration:
    """自由探索：LangGraph 逐层地牢决策循环（interrupt/resume + checkpointer）。

    每层搜节点 → 决策 → 进节点/下楼，循环至撤退或触底。
    """

    def __init__(
        self,
        llm: LlmClient,
        evaluator: Evaluator,
        tools: ToolRegistry,
        bus: EventBus,
        exploration_config: ExplorationConfig,
    ) -> None:
        self._llm = llm
        self._evaluator = evaluator
        self._tools = tools
        self._bus = bus
        self._web_enabled = exploration_config.web_enabled
        self._checkpointer = InMemorySaver()
        self._graph = self._build_graph()

    def _build_graph(self) -> CompiledStateGraph[ExplorationState]:
        g = StateGraph(ExplorationState)
        g.add_node("enter_floor", self._enter_floor)
        g.add_node("decide", self._decide)
        g.add_node("visit_node", self._visit_node)
        g.add_node("safe_room", self._safe_room)
        g.add_node("descend", self._descend)
        g.add_node("finalize", self._finalize)
        g.add_edge(START, "enter_floor")
        g.add_edge("enter_floor", "decide")
        g.add_conditional_edges(
            "decide",
            self._route_from_decide,
            {"visit_node": "visit_node", "safe_room": "safe_room",
             "descend": "descend", "finalize": "finalize"},
        )
        g.add_edge("visit_node", "decide")
        g.add_edge("safe_room", "decide")
        g.add_edge("descend", "enter_floor")
        g.add_edge("finalize", END)
        return g.compile(checkpointer=self._checkpointer)

    # ---- 逐层 run 驱动 ----

    async def start(
        self,
        seed_desire_id: str | None,
        seed_topic: str,
        energy: float,
        activity_id: str,
        correlation_id: str,
    ) -> ExplorationProgress:
        initial: ExplorationState = {
            "seed_desire_id": seed_desire_id,
            "seed_topic": seed_topic,
            "focus": seed_topic,
            "floor": 1,
            "energy": min(_MAX_ENERGY, max(0.0, energy)),
            "autopilot": False,
            "current_nodes": [],
            "visited": [],
            "findings": [],
            "knowledge": [],
            "new_topics": [],
            "strong_new_topics": [],
            "encounters": [],
            "loot": [],
            "npcs": [],
            "summary": "",
            "core_discovery": "",
            "outcome": "",
            "retreated": False,
            "_route": "",
            "_choice": None,
            "_last_node": None,
            "activity_id": activity_id,
            "correlation_id": correlation_id,
        }
        config: RunnableConfig = {"configurable": {"thread_id": activity_id}}
        out = await self._graph.ainvoke(initial, config)
        progress = self._to_progress(out)
        await self._broadcast(progress)
        return progress

    async def resume(self, activity_id: str, choice: str) -> ExplorationProgress:
        config: RunnableConfig = {"configurable": {"thread_id": activity_id}}
        out = await self._graph.ainvoke(Command(resume=choice), config)
        progress = self._to_progress(out)
        await self._broadcast(progress)
        return progress

    def _to_progress(self, out: dict[str, Any]) -> ExplorationProgress:
        if "__interrupt__" in out:
            interrupts = out["__interrupt__"]
            payload = cast(dict[str, Any], interrupts[0].value)
            state = {k: v for k, v in out.items() if k != "__interrupt__"}
            return {"pending": True, "decision": payload, "result": {}, "state": state}
        state = cast(ExplorationState, out)
        return {
            "pending": False,
            "decision": {},
            "result": assemble_result(state),
            "state": cast(dict[str, Any], state),
        }

    async def _broadcast(self, progress: ExplorationProgress) -> None:
        if not progress["pending"]:
            return
        await self._bus.publish(internal_event(
            EventType.EXPLORATION_STEP,
            {
                "activity_id": progress["state"]["activity_id"],
                "decision": progress["decision"],
            },
            str(progress["state"]["correlation_id"]),
        ))

    # ---- 图节点 ----

    async def _enter_floor(self, state: ExplorationState) -> ExplorationState:
        nodes = await self._search_nodes(state["focus"], state["floor"])
        state["current_nodes"] = fill_dead_ends(nodes)
        return state

    async def _decide(self, state: ExplorationState) -> ExplorationState:
        if state["retreated"] or state["energy"] <= 0.0:
            return state
        payload = {
            "kind": "choose",
            "floor": state["floor"],
            "energy": state["energy"],
            "focus": state["focus"],
            "nodes": state["current_nodes"],
        }
        choice = cast(str, interrupt(payload))
        route, idx = parse_choice(choice, cast(dict[str, Any], state))
        if route == "retreat":
            state["retreated"] = True
        state["_route"] = route
        state["_choice"] = idx
        return state

    def _route_from_decide(self, state: ExplorationState) -> str:
        if state["retreated"] or state["energy"] <= 0.0:
            return "finalize"
        if state["_route"] == "descend" and state["floor"] >= _MAX_DEPTH:
            return "finalize"  # 触底：不再下楼，交给 finalize 兜底判定
        return {"visit": "visit_node", "safe_room": "safe_room",
                "descend": "descend"}[state["_route"]]

    async def _visit_node(self, state: ExplorationState) -> ExplorationState:
        idx = state["_choice"]
        node = state["current_nodes"][idx] if idx is not None else None
        if node is None:
            return state
        state["energy"] -= enter_cost(node)
        state["visited"].append(
            {"floor": state["floor"], "name": node["name"], "kind": node["kind"]}
        )
        if node["kind"] == _KIND_DEAD_END:
            state["findings"].append(f"死路：{node['name']}（无收获）")
        else:
            content = node["snippet"]
            if node["url"]:
                try:
                    content = str(
                        await self._tools.call("web_fetch", {"url": node["url"]})
                    )
                except Exception:
                    pass  # best-effort：下载正文失败不崩 run，snippet 兜底
            state["findings"].append(f"{node['name']}：{content}")
        state["_last_node"] = node
        return state

    async def _safe_room(self, state: ExplorationState) -> ExplorationState:
        state["energy"] = restore_energy(state["energy"])
        state["visited"].append(
            {"floor": state["floor"], "name": "安全房", "kind": _KIND_SAFE_ROOM}
        )
        return state

    async def _descend(self, state: ExplorationState) -> ExplorationState:
        state["energy"] -= descent_cost(state["floor"])
        # 追真实线索：取最近一条真实发现作下一层主题；无则沿用 focus 深挖
        clue = next(
            (f for f in reversed(state["findings"]) if not f.startswith("死路：")),
            None,
        )
        if clue is not None:
            state["focus"] = clue[-80:]
        state["floor"] += 1
        return state

    async def _finalize(self, state: ExplorationState) -> ExplorationState:
        # Task 4 补终局 LLM 判定；本 Task 先用空实现占位（outcome 走兜底撤退）
        state["summary"] = state["seed_topic"]
        state["core_discovery"] = ""
        state["outcome"] = determine_outcome(
            state["energy"], state["core_discovery"], state["retreated"]
        )
        return state

    async def pick_topic(self, correlation_id: str) -> str:
        """好奇驱动选题：无用户指定主题时，让尼克斯自己定一个可上网搜索的话题。"""
        output = await self._llm.complete(
            [
                {"role": "system", "content": _EXPLORATION_TOPIC_SYSTEM},
                {"role": "user", "content": "给一个你今天好奇、想上网搜索探索的主题"},
            ],
            module="activity",
            output_type="exploration_topic",
            correlation_id=correlation_id,
            json_mode=True,
        )
        await self._evaluator.evaluate(output)
        plan = json.loads(output.content)
        if not isinstance(plan, dict):
            raise ValueError(f"探索选题 JSON 应是对象，得到 {type(plan).__name__}")
        plan = cast(dict[str, Any], plan)
        return str(plan.get("topic") or "有趣的新鲜事")

    async def _search_nodes(self, topic: str, floor: int) -> list[FloorNode]:
        """本层真实搜索 → FloorNode 列表；搜不到返回空（交给 fill_dead_ends 补死路）。

        险节点 = 深楼层（floor >= 3），进后由 facade 触发有根遭遇。
        """
        if self._web_enabled:
            res = await self._tools.call("web_search", {"query": topic})
            if not res:
                res = await self._tools.call("local_search", {"query": topic})
        else:
            res = await self._tools.call("local_search", {"query": topic})
        nodes: list[FloorNode] = []
        for r in res[:_NODE_SLOTS]:
            node = self._node_from_result(r, floor)
            if node is not None:
                nodes.append(node)
        return nodes

    def _node_from_result(self, result: Any, floor: int) -> FloorNode | None:
        """把一条检索结果转成 FloorNode；无法解析出名称则返回 None。"""
        if isinstance(result, dict):
            title = cast(str | None, result.get("title"))
            name_key = cast(str | None, result.get("name"))
            url = str(cast(str | None, result.get("url")) or "")
            snippet = str(
                cast(str | None, result.get("snippet"))
                or cast(str | None, result.get("content"))
                or ""
            )
            name = title or name_key or (_domain(url) if url else "")
        elif isinstance(result, str):
            name, url, snippet = result, "", ""
        else:
            return None
        if not name:
            return None
        return {
            "name": name, "url": url, "kind": _KIND_REAL,
            "snippet": snippet, "may_encounter": floor >= 3,
        }


_EXPLORATION_TOPIC_SYSTEM = (
    "你是尼克斯。给一个具体、可上网搜索的探索主题，按 JSON 输出 {topic}。"
)


def _domain(url: str) -> str:
    """网页节点名兜底：title 缺失时用域名。"""
    host = urlparse(url).hostname
    return host or url
