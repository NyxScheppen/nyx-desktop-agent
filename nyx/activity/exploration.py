# pyright: reportMissingTypeStubs=false, reportUnknownMemberType=false
# langgraph 类型标注松散：add_node/compile/ainvoke 返回部分未知、graph.state 缺 stub
import json
from collections.abc import Hashable
from typing import Any, TypedDict, cast
from urllib.parse import urlparse

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from nyx.config import ExplorationConfig
from nyx.enums import EventType
from nyx.eval.evaluator import Evaluator
from nyx.events.bus import EventBus
from nyx.events.event import SECONDS_PER_HOUR, internal_event
from nyx.llm.client import LlmClient
from nyx.tools.registry import ToolRegistry

_MAX_STEPS = 8                    # 探索链最大步数（可推翻）

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


class ExplorationNode(TypedDict):
    name: str
    url: str
    kind: str  # "search"（搜索动作，url 空）| "web"（访问的网页）


class ExplorationState(TypedDict):
    seed: str
    focus: str
    findings: list[str]
    notes: list[str]
    nodes: list[ExplorationNode]
    step: int
    done: bool
    activity_id: str
    correlation_id: str


def should_explore(last_explored_at: float, rate_limit_hours: int, now: float) -> bool:
    """自由探索升级门槛（纯函数）：仅频率上限。

    「探索欲」条件由调用方结构保证：READING 活动仅由 DesireType.EXPLORATION 映射而来，
    故调用方在 activity.type is READING 时才调本函数。
    精力不再单独卡：探索消耗 -30，精力不足由 build_schedule 的 REST 穿插兜底。
    """
    return now - last_explored_at >= rate_limit_hours * SECONDS_PER_HOUR


class Exploration:
    """跨域行为链（LangGraph）：好奇 → 搜索 → 写笔记（design §8.6）。

    「读」不单列节点：联网时 _search_web 内已用 web_fetch 下载正文入书库并触发读书
    （design §8.6 主动下载），本地时 local_search 直接返回片段。
    故链上只有搜索 + 写笔记。
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
        # 联网为主通道：web 开启时 search_web 是主搜索动作，
        # search_local 作兜底（进 _search_web 内）
        if self._web_enabled:
            self._actions = ["search_web", "write_note"]
        else:
            self._actions = ["search_local", "write_note"]
        self._graph = self._build_graph()

    def _build_graph(self) -> CompiledStateGraph[ExplorationState]:
        g = StateGraph(ExplorationState)
        g.add_node("plan_next", self._plan_next)
        g.add_node("search_local", self._search_local)
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

    async def run(
        self, seed: str, activity_id: str, correlation_id: str
    ) -> dict[str, Any]:
        initial: ExplorationState = {
            "seed": seed, "focus": seed, "findings": [], "notes": [],
            "nodes": [], "step": 0, "done": False,
            "activity_id": activity_id, "correlation_id": correlation_id,
        }
        result = await self._graph.ainvoke(initial)
        return {
            "findings": result["findings"],
            "notes": result["notes"],
            "nodes": result["nodes"],
        }

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
        await self._record_node(
            state, {"name": f"搜索：{state['focus']}", "url": "", "kind": "search"}
        )
        res = await self._tools.call("local_search", {"query": state["focus"]})
        state["findings"].extend(str(r) for r in res)
        return state

    async def _search_web(self, state: ExplorationState) -> ExplorationState:
        focus = state["focus"]
        await self._record_node(
            state, {"name": f"搜索：{focus}", "url": "", "kind": "search"}
        )
        res = await self._tools.call("web_search", {"query": focus})
        if not res:
            # 联网失败/无结果 → 本地兜底（web_enabled 时 search_local 不再独立轮转）
            res = await self._tools.call("local_search", {"query": focus})
        state["findings"].extend(str(r) for r in res)
        if res:
            first = res[0]
            if isinstance(first, dict) and first.get("url"):
                first = cast(dict[str, Any], first)
                url = first["url"]
                name = first.get("title") or _domain(url)
                await self._record_node(
                    state, {"name": name, "url": url, "kind": "web"}
                )
                # 顺手下第一条正文入书库；失败静默不崩探索
                try:
                    fetched = await self._tools.call("web_fetch", {"url": url})
                    state["findings"].append(f"已下载资料：{fetched}")
                except Exception:
                    pass
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

    async def _record_node(
        self, state: ExplorationState, node: ExplorationNode
    ) -> None:
        """记一条探索节点并广播 EXPLORATION_STEP（前端地图实时点亮）。"""
        state["nodes"].append(node)
        await self._bus.publish(internal_event(
            EventType.EXPLORATION_STEP,
            {"activity_id": state["activity_id"], "node": dict(node)},
            state["correlation_id"],
        ))

    def _route(self, state: ExplorationState) -> str:
        if state["done"]:
            return "finalize"
        # MVP：确定性轮转（与 self._actions 对齐，2 步一轮），不靠 LLM 选具体动作。
        # web 开启时 actions[0]=search_web 起始；web 关闭时 =search_local 起始。
        # step 在 _plan_next 里先 +1，故 -1 对齐到 actions[0]。
        return self._actions[(state["step"] - 1) % len(self._actions)]


_EXPLORATION_PLAN_SYSTEM = (
    "你是尼克斯的探索规划器。按 JSON 输出 {focus, done}，"
    "决定下一步聚焦对象与是否结束。"
)


_EXPLORATION_TOPIC_SYSTEM = (
    "你是尼克斯。给一个具体、可上网搜索的探索主题，按 JSON 输出 {topic}。"
)


def _domain(url: str) -> str:
    """网页节点名兜底：title 缺失时用域名。"""
    host = urlparse(url).hostname
    return host or url
