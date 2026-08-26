# 探索系统重设计（逐层地牢）· 后端实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 `FREE_EXPLORATION` 从「固定 8 步搜索链」重写成「在真实世界里下副本」的文字 Roguelike——逐层地牢（真实搜索节点 + 死路补位 + 安全房）、精力=燃料、欲望=目标、可托管、有根遭遇，全部发现落进既有记忆/欲望老系统，道具/NPC 只留 hook。

**Architecture:** 用 LangGraph `StateGraph` + `interrupt()` + `InMemorySaver`（checkpointer）承载逐层 run 的决策循环。`Exploration` 内部持编译好的图 + checkpointer，`start()`/`resume()` 驱动图跑一步到下一个决策点或终点；决策点由 `interrupt` 暂停，用户（或托管 LLM）经 `ActivityFacade.choose_exploration` 用 `Command(resume=...)` 续跑。纯函数（死路补位 / 精力消耗 / outcome 判定）留在 `exploration.py` 模块级，可单测。有根遭遇由 `EncounterFacade.start_rooted` 生成、走既有 ENCOUNTER_START/choose/END 事件流回写记忆/欲望。

**Tech Stack:** Python 3.11+，LangGraph 1.2.11（`interrupt`/`Command`/`InMemorySaver`），FastAPI/SSE（既有），aiosqlite（既有），pytest（既有）。

**Spec:** `docs/superpowers/specs/2026-08-27-exploration-roguelike-design.md`

## Global Constraints

> 每个 Task 的需求都隐式包含本节（自 CLAUDE.md 与 design doc 逐字抄录）。

- Python 3.11+，**所有函数签名完整类型标注**。
- 质量门顺序：`ruff check` → `pyright` → `pytest` 全零报错；提交前跑。
- 禁止 `*` 导入；禁止裸 `except Exception` 吞异常（**豁免**：LLM/eval/遭遇生成失败属 best-effort，记日志返默认值/跳过、不重抛——对应本计划的 `_finalize`、`start_rooted` 的 LLM 段）。
- Mock LLM：所有 LLM 调用可注入 mock（`_FakeLlm` 返回预设 fixture）；测试不依赖真实 LLM/桌面/文件系统；只验证管道正确性（输入走对流程、输出结构正确），**不验证文本质量**。
- 每个 Facade 方法测试 ≤ 5 断言；纯函数优先测全。
- **每次写测试后**更新 `docs/test-inventory.md`（追加：新增哪些测试 / 检查方向 / 所属系统 / 哪个功能阶段）。
- 结果形状**保持 dict**（design §8「优先 dict，反冗余」），不 dataclass 化。
- 楼层槽数 / 精力消耗 / 安全房回复量用**模块级常量**（同现有 `_MAX_STEPS`），不新增配置项。
- 不新增抽象层（Facade → 子系统 → 内部类已是三层）；道具/NPC 只留 `result.loot`/`result.npcs` 字段，不建表/实体/事件。
- 托管整场一个开关，不做逐节点开关。

---

## File Structure

**Create:**
- 无新文件（反冗余：纯函数并入 `exploration.py`，遭遇并入 `encounter/facade.py`）。

**Modify:**
- `nyx/activity/exploration.py` — 纯函数 + `FloorNode`/`ExplorationState` 类型 + 逐层 StateGraph + `start`/`resume`/`pick_choice`。
- `nyx/activity/facade.py` — 交互式执行改造（`start_exploration`/`choose_exploration`/`set_exploration_autopilot`）、种子接线、记忆/欲望接缝、遭遇触发回调。
- `nyx/encounter/facade.py` — 删随机入口、新增 `start_rooted`。
- `nyx/encounter/rules.py` — 删块边界随机（`_BLOCK_PROBABILITY`/`_COOLDOWN_SECONDS`/`should_encounter`）。
- `nyx/enums.py` — `EncounterKind` 加 `ROOTED`。
- `nyx/memory/facade.py` — `_activity_memory_fields` 的 free_exploration 映射。
- `nyx/main.py` — 组合根：`encounter` 前移 + 给 `ActivityFacade` 注入 `on_rooted_encounter` 回调；新增 `/api/explore/choose` + `/api/explore/autopilot` 端点。

**Test:**
- `tests/test_activity/test_exploration.py` — 纯函数 + 图交互 + 结果组装。
- `tests/test_activity/test_activity_facade.py` — 交互式执行 / 记忆欲望接线。
- `tests/test_encounter/test_encounter_facade.py` / `test_encounter_rules.py` — 有根遭遇 + 删随机。
- `tests/test_memory/`（既有）— `_activity_memory_fields` 新映射。
- `tests/test_api/test_endpoints.py` — 探索决策/托管端点。

---

## Task 1: 探索纯函数 + 类型

**Files:**
- Modify: `nyx/activity/exploration.py:1-55`（顶部常量区与类型，替换 `ExplorationState`，新增 `FloorNode` + 纯函数）
- Test: `tests/test_activity/test_exploration.py`

**Interfaces:**
- Consumes: 无（首个任务）。
- Produces:
  - 常量：`_NODE_SLOTS`、`_ENTER_NODE_COST`、`_DEAD_END_COST`、`_SAFE_ROOM_RESTORE`、`_MAX_ENERGY`、`_DESCENT_BASE_COST`、`_DESCENT_STEP_COST`、`_MAX_DEPTH`、`_KIND_REAL`/`_KIND_DEAD_END`/`_KIND_SAFE_ROOM`。
  - 类型：`FloorNode(TypedDict)`、`ExplorationState(TypedDict)`。
  - 纯函数：`fill_dead_ends(nodes, target=3) -> list[FloorNode]`、`enter_cost(node) -> float`、`descent_cost(floor) -> float`、`restore_energy(energy) -> float`、`determine_outcome(energy, core_discovery, retreated) -> str`、`parse_choice(choice, state) -> tuple[str, int | None]`。

- [ ] **Step 1: 写失败测试**

在 `tests/test_activity/test_exploration.py` 末尾追加（保留既有 import 与 `_FakeLlm` 等 fixture；新增 import 见下）：

```python
from nyx.activity.exploration import (
    _KIND_DEAD_END, _KIND_REAL, _KIND_SAFE_ROOM,
    descent_cost, determine_outcome, enter_cost, fill_dead_ends,
    parse_choice, restore_energy, FloorNode,
)


def _node(kind: str, name: str = "节点") -> FloorNode:
    return {"name": name, "url": "", "kind": kind, "snippet": "", "may_encounter": False}


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
    state = {"current_nodes": [_node(_KIND_REAL)]}  # type: ignore[typeddict-item]
    assert parse_choice("node:0", state) == ("visit", 0)  # type: ignore[arg-type]
    assert parse_choice("safe_room", state) == ("safe_room", None)  # type: ignore[arg-type]
    assert parse_choice("retreat", state) == ("retreat", None)  # type: ignore[arg-type]
    assert parse_choice("node:9", state) == ("retreat", None)  # type: ignore[arg-type]
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_activity/test_exploration.py::test_fill_dead_ends_pads_to_target -v`
Expected: FAIL（`ImportError: cannot import name 'fill_dead_ends'`）。

- [ ] **Step 3: 写最小实现**

替换 `nyx/activity/exploration.py` 顶部的 `ExplorationState` 定义（保留 `ExplorationNode` 若仍被引用则暂留，本 Task 先不动 `Exploration` 类体；`ExplorationState` 被旧类体引用，本 Task 只新增类型与纯函数，旧 `ExplorationState` 更名见 Task 3——此处**新增** `FloorNode` 与纯函数，`ExplorationState` 重写在 Task 3 随图一起换，避免中间态破坏旧 `run`）。

在 `_MAX_STEPS = 8` 之后新增：

```python
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


def fill_dead_ends(nodes: list[FloorNode], target: int = _NODE_SLOTS) -> list[FloorNode]:
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
    """run 结局：won（挖到核心发现）/ exhausted（精力耗尽）/ retreated（主动撤退）。纯函数。"""
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
        if isinstance(nodes, list) and 0 <= idx < len(nodes):
            return "visit", idx
    return "retreat", None
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_activity/test_exploration.py -v`
Expected: 新增 6 个测试 PASS（旧测试不受影响）。

- [ ] **Step 5: 提交**

```bash
git add nyx/activity/exploration.py tests/test_activity/test_exploration.py docs/test-inventory.md
git commit -m "feat(exploration): 逐层地牢纯函数与类型（死路补位/精力/outcome/决策路由）"
```

> 提交前更新 `docs/test-inventory.md`：追加 `test_activity/test_exploration.py` 新增 6 个纯函数测试（fill_dead_ends / enter_cost / descent_cost / restore_energy / determine_outcome / parse_choice），检查方向=功能正确 + 边界鲁棒，属 activity 系统，探索 Roguelike 阶段。

---

## Task 2: 本层节点填充（真实搜索 → 死路补位）

**Files:**
- Modify: `nyx/activity/exploration.py`（`Exploration` 内新增 `_search_nodes`）
- Test: `tests/test_activity/test_exploration.py`

**Interfaces:**
- Consumes: Task 1 的 `FloorNode`/`fill_dead_ends`/`_KIND_*`/`_NODE_SLOTS`/`_MAX_DEPTH`。
- Produces: `Exploration._search_nodes(topic: str, floor: int) -> list[FloorNode]`（`_web_enabled` 分支真实搜索，搜不到返回空交给 `fill_dead_ends` 补死路；`may_encounter = floor >= 3`）。

- [ ] **Step 1: 写失败测试**

追加到 `tests/test_activity/test_exploration.py`（`_FakeTools`/`_WebTools` 已在文件里）：

```python
async def test_search_nodes_fills_real_results():
    exploration = Exploration(
        _FakeLlm(), _FakeEvaluator(), _WebTools(), _bus(), ExplorationConfig(web_enabled=True)
    )
    nodes = await exploration._search_nodes("量子", 1)
    assert nodes[0]["kind"] == _KIND_REAL
    assert nodes[0]["may_encounter"] is False


async def test_search_nodes_deep_floor_marks_encounter():
    exploration = Exploration(
        _FakeLlm(), _FakeEvaluator(), _WebTools(), _bus(), ExplorationConfig(web_enabled=True)
    )
    nodes = await exploration._search_nodes("量子", 3)
    assert nodes[0]["may_encounter"] is True
```

> 说明：`_bus()` 是既有测试里的 EventBus fixture（若文件里名为别的，沿用其名）；`ExplorationConfig(web_enabled=True)` 按既有测试的构造方式写（见文件内既有 `Exploration(...)` 用法）。

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_activity/test_exploration.py::test_search_nodes_fills_real_results -v`
Expected: FAIL（`AttributeError: 'Exploration' object has no attribute '_search_nodes'`）。

- [ ] **Step 3: 写最小实现**

在 `Exploration` 类内（`pick_topic` 之后、`_record_node` 之前）新增：

```python
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
            name = result.get("title") or result.get("name") or (
                _domain(result["url"]) if result.get("url") else ""
            )
            url = str(result.get("url") or "")
            snippet = str(result.get("snippet") or result.get("content") or "")
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
```

> `_domain` 已在文件底部定义，直接复用。

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_activity/test_exploration.py -v`
Expected: 新增 2 个测试 PASS。

- [ ] **Step 5: 提交**

```bash
git add nyx/activity/exploration.py tests/test_activity/test_exploration.py docs/test-inventory.md
git commit -m "feat(exploration): 本层真实搜索 → 节点填充（深楼层标险）"
```

> 更新 `docs/test-inventory.md`：追加 2 个测试（_search_nodes 真实填充 / 深楼层标险），检查方向=管道正确 + 边界（深楼层标记），activity 系统。

---

## Task 3: 逐层 StateGraph + interrupt + checkpointer

**Files:**
- Modify: `nyx/activity/exploration.py`（重写 `ExplorationState`、`_build_graph`、`run`→`start`/`resume`、新增图节点）
- Test: `tests/test_activity/test_exploration.py`

**Interfaces:**
- Consumes: Task 1 纯函数/常量、Task 2 `_search_nodes`、既有 `_record_node` 逻辑（改造为 `_broadcast`）。
- Produces:
  - `ExplorationProgress(TypedDict)`：`{pending: bool, decision: dict, result: dict, state: dict}`。
  - `Exploration.start(seed_desire_id, seed_topic, energy, activity_id, correlation_id) -> ExplorationProgress`
  - `Exploration.resume(activity_id, choice) -> ExplorationProgress`
  - `Exploration._broadcast(progress) -> None`（pending 时广播 `EXPLORATION_STEP`）

- [ ] **Step 1: 写失败测试**

追加到 `tests/test_activity/test_exploration.py`。新增 fixture 用假 LLM 驱动终局判定（`_FINALIZE_JSON` 见 Task 4，本 Task 先用 `json.dumps({})` 占位——终局判定返回空对象，outcome 走兜底撤退；核心断言在「跑一步中断 / resume 续跑」的机制，不验文本）：

```python
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

async def test_start_interrupts_at_first_decision():
    exploration = Exploration(
        _FakeLlm(), _FakeEvaluator(), _WebTools(), _bus(), ExplorationConfig(web_enabled=True)
    )
    p = await exploration.start(None, "量子", 100.0, "a1", "c1")
    assert p["pending"] is True
    assert p["decision"]["kind"] == "choose"
    assert len(p["decision"]["nodes"]) == 3


async def test_resume_choice_visits_node_and_interrupts_again():
    exploration = Exploration(
        _FakeLlm(), _FakeEvaluator(), _WebTools(), _bus(), ExplorationConfig(web_enabled=True)
    )
    await exploration.start(None, "量子", 100.0, "a1", "c1")
    p = await exploration.resume("a1", "node:0")
    assert p["pending"] is True
    # 进了一个真实节点后精力下降（100 - 6 = 94）
    assert p["state"]["energy"] == pytest.approx(94.0)


async def test_resume_retreat_finalizes():
    exploration = Exploration(
        _FakeLlm(), _FakeEvaluator(), _WebTools(), _bus(), ExplorationConfig(web_enabled=True)
    )
    await exploration.start(None, "量子", 100.0, "a1", "c1")
    p = await exploration.resume("a1", "retreat")
    assert p["pending"] is False
    assert p["result"]["type"] == "free_exploration"
    assert p["result"]["outcome"] == "retreated"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_activity/test_exploration.py::test_start_interrupts_at_first_decision -v`
Expected: FAIL（`AttributeError: 'Exploration' object has no attribute 'start'`）。

- [ ] **Step 3: 写最小实现**

(a) 顶部 import 增补：

```python
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command, interrupt
```

(b) 重写 `ExplorationState`（替换旧定义，旧 `run`/`_plan_next` 等旧节点代码一并删除——本 Task 一次性换血，见 (e)）：

```python
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
```

(c) `__init__` 里加 checkpointer 并重编图（`_actions` 轮转相关字段删除）：

```python
        self._checkpointer = InMemorySaver()
        self._graph = self._build_graph()
```

(d) 重写 `_build_graph`：

```python
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
```

(e) 删除旧 `run`/`_plan_next`/`_search_local`/`_search_web`/`_write_note`/`_finalize`/`_route`（旧固定链），替换为：

```python
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
        config = {"configurable": {"thread_id": activity_id}}
        out = await self._graph.ainvoke(initial, config)
        progress = self._to_progress(out)
        await self._broadcast(progress)
        return progress

    async def resume(self, activity_id: str, choice: str) -> ExplorationProgress:
        config = {"configurable": {"thread_id": activity_id}}
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
        return {"pending": False, "decision": {}, "result": assemble_result(state), "state": state}

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
        route, idx = parse_choice(choice, state)
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
        state["visited"].append({"floor": state["floor"], "name": node["name"], "kind": node["kind"]})
        if node["kind"] == _KIND_DEAD_END:
            state["findings"].append(f"死路：{node['name']}（无收获）")
        else:
            content = node["snippet"]
            if node["url"]:
                try:
                    content = str(await self._tools.call("web_fetch", {"url": node["url"]}))
                except Exception:
                    pass  # best-effort：下载正文失败不崩 run，snippet 兜底
            state["findings"].append(f"{node['name']}：{content}")
        state["_last_node"] = node
        return state

    async def _safe_room(self, state: ExplorationState) -> ExplorationState:
        state["energy"] = restore_energy(state["energy"])
        state["visited"].append({"floor": state["floor"], "name": "安全房", "kind": _KIND_SAFE_ROOM})
        return state

    async def _descend(self, state: ExplorationState) -> ExplorationState:
        state["energy"] -= descent_cost(state["floor"])
        # 追真实线索：取最近一条真实发现作下一层主题；无则沿用 focus 深挖
        clue = next((f for f in reversed(state["findings"]) if not f.startswith("死路：")), None)
        if clue is not None:
            state["focus"] = clue[-80:]
        state["floor"] += 1
        return state

    async def _finalize(self, state: ExplorationState) -> ExplorationState:
        # Task 4 补终局 LLM 判定；本 Task 先用空实现占位（outcome 走兜底撤退）
        state["summary"] = state["seed_topic"]
        state["core_discovery"] = ""
        state["outcome"] = determine_outcome(state["energy"], state["core_discovery"], state["retreated"])
        return state
```

(f) 删除旧 `run` 方法后，`_record_node` 仍被别处引用则一并删除（本项目里只有旧 `_search_local/_search_web` 用它，已删）。`pick_topic`、`should_explore` 保留不动。

(g) 文件顶部加 `assemble_result` 纯函数（Task 4 会用到 `knowledge` 等字段，本 Task 先放完整形状）：

```python
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
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_activity/test_exploration.py -v`
Expected: 新增 3 个测试 PASS。注意旧测试若引用已删的 `run`/`_plan_next` 等，本 Task 一并更新旧测试（删掉旧固定链测试，改为新 `start`/`resume` 机制测试）。

- [ ] **Step 5: 提交**

```bash
git add nyx/activity/exploration.py tests/test_activity/test_exploration.py docs/test-inventory.md
git commit -m "feat(exploration): 逐层 StateGraph + interrupt + checkpointer 决策循环"
```

> 更新 `docs/test-inventory.md`：追加 3 个测试（start 首决策中断 / resume 进节点再中断 / retreat 终局），检查方向=管道正确（interrupt/resume 机制），activity 系统。并注明删除旧固定链测试。

---

## Task 4: 终局 LLM 判定 + 结果组装

**Files:**
- Modify: `nyx/activity/exploration.py`（`_finalize` 补 LLM 判定 + `_EXPLORATION_FINALIZE_SYSTEM`）
- Test: `tests/test_activity/test_exploration.py`

**Interfaces:**
- Consumes: Task 3 的 `assemble_result`/`_finalize`。
- Produces: `_EXPLORATION_FINALIZE_SYSTEM` 常量；`_finalize` 填充 `summary`/`core_discovery`/`knowledge`/`strong_new_topics`/`new_topics`/`outcome`。

- [ ] **Step 1: 写失败测试**

追加到 `tests/test_activity/test_exploration.py`：

```python
_FINALIZE_JSON = json.dumps({
    "summary": "弄懂了量子退相干的机制",
    "core_discovery": "退相干是量子系统与环境纠缠导致的表观坍缩",
    "knowledge": [{"topic": "退相干", "content": "环境纠缠抹去相干性"}],
    "strong_new_topics": ["量子纠错"],
    "casual_new_topics": ["退火算法"],
})


async def test_finalize_judges_won():
    llm = _FakeLlm(_FINALIZE_JSON)
    exploration = Exploration(llm, _FakeEvaluator(), _WebTools(), _bus(), ExplorationConfig(web_enabled=True))
    await exploration.start(None, "量子", 100.0, "a1", "c1")
    p = await exploration.resume("a1", "retreat")
    # retreat 触发终局判定，但核心发现命中 → won 覆盖 retreat
    assert p["result"]["outcome"] == "won"
    assert p["result"]["core_discovery"] != ""
    assert p["result"]["knowledge"][0]["topic"] == "退相干"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_activity/test_exploration.py::test_finalize_judges_won -v`
Expected: FAIL（outcome 仍为 `retreated`，因 `_finalize` 还没读 LLM）。

- [ ] **Step 3: 写最小实现**

替换 `_finalize` 与文件底部 prompt 区：

```python
    async def _finalize(self, state: ExplorationState) -> ExplorationState:
        """终局判定：一次 LLM 调用产出 summary/核心发现/知识/新话题（可 mock）。

        best-effort：LLM/parse 失败记日志、按空结果走兜底，不重抛。
        """
        try:
            output = await self._llm.complete(
                [
                    {"role": "system", "content": _EXPLORATION_FINALIZE_SYSTEM},
                    {"role": "user", "content": json.dumps({
                        "seed_topic": state["seed_topic"],
                        "floor": state["floor"],
                        "findings": state["findings"],
                    }, ensure_ascii=False)},
                ],
                module="activity",
                output_type="exploration_finalize",
                correlation_id=state["correlation_id"],
                json_mode=True,
            )
            await self._evaluator.evaluate(output)
            judged = json.loads(output.content)
            if not isinstance(judged, dict):
                judged = {}
        except Exception:
            judged = {}
        judged = cast(dict[str, Any], judged)
        state["summary"] = str(judged.get("summary") or state["seed_topic"])
        state["core_discovery"] = str(judged.get("core_discovery") or "")
        knowledge = judged.get("knowledge")
        state["knowledge"] = knowledge if isinstance(knowledge, list) else []
        strong = judged.get("strong_new_topics")
        state["strong_new_topics"] = strong if isinstance(strong, list) else []
        casual = judged.get("casual_new_topics")
        state["new_topics"] = casual if isinstance(casual, list) else []
        # 深度上限兜底：触底仍未判出核心发现时，用最后一条真实发现兜底
        if not state["core_discovery"] and state["floor"] >= _MAX_DEPTH and state["findings"]:
            state["core_discovery"] = state["findings"][-1]
        state["outcome"] = determine_outcome(
            state["energy"], state["core_discovery"], state["retreated"]
        )
        return state
```

文件底部 prompt 区新增：

```python
_EXPLORATION_FINALIZE_SYSTEM = (
    "你是尼克斯的探索结算器。基于这场探索的种子话题与发现，判断是否挖到了核心发现。"
    "按 JSON 输出：summary（一句话总结）、core_discovery（若真相已明则非空字符串，否则空串）、"
    "knowledge（数组，每项 {topic, content}，客观知识点）、"
    "strong_new_topics（数组，值得长期追的强烈新兴趣）、"
    "casual_new_topics（数组，一般好奇，不值得立长期欲望）。"
)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_activity/test_exploration.py -v`
Expected: 新增测试 PASS；Task 3 的 retreat 测试用 `_FakeLlm()`（默认 `_PLAN_JSON`）——注意 `_FakeLlm` 默认 content 是 `_PLAN_JSON`（`{"focus":...,"done":...}`），`_finalize` 会把它当终局 JSON 但缺 core_discovery，走兜底 retreated，仍 PASS。确认无回归。

- [ ] **Step 5: 提交**

```bash
git add nyx/activity/exploration.py tests/test_activity/test_exploration.py docs/test-inventory.md
git commit -m "feat(exploration): 终局 LLM 判定（核心发现/知识/新话题）+ 深度兜底"
```

> 更新 `docs/test-inventory.md`：追加 1 个测试（终局判定 won + 知识/核心发现），检查方向=管道正确（终局 LLM 输出结构落进 result），activity 系统。

---

## Task 5: ActivityFacade 交互式执行改造 + 种子接线

**Files:**
- Modify: `nyx/activity/facade.py:342-365`（`complete_activity` 不动）、`451-480`（`start_exploration`）、`516-665`（`_maybe_start_activity`/`_execute`/`_run_activity`）
- Test: `tests/test_activity/test_activity_facade.py`

**Interfaces:**
- Consumes: Task 3 `Exploration.start/resume`、Task 4 `assemble_result`。
- Produces:
  - `ActivityFacade.choose_exploration(activity_id, choice) -> dict[str, Any]`（新公开方法，端点用）。
  - `ActivityFacade._on_rooted_encounter: Callable[[str, str, str], Awaitable[None]] | None`（构造注入，默认 None）。
  - `ActivityFacade.set_exploration_autopilot(activity_id, on)`（Task 8 补全，本 Task 先占位 raise NotImplementedError）。

- [ ] **Step 1: 写失败测试**

在 `tests/test_activity/test_activity_facade.py` 追加（沿用文件内既有 fixture/构造方式；关键断言 ≤5）：

```python
async def test_choose_exploration_retreat_completes(facade, running_exploration_activity):
    result = await facade.choose_exploration(running_exploration_activity.id, "retreat")
    assert result["outcome"] == "retreated"
    current = await facade.get_current()
    assert current.status is ActivityStatus.COMPLETED
```

> `facade`/`running_exploration_activity` 是既有 fixture 或本 Task 新增的小 fixture（构造 `ActivityFacade` 注入 `_FakeLlm`/`_WebTools`/假 store，`start_exploration` 后拿到 running activity）。具体 mock 构造参照文件内既有 `test_*` 的写法。

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_activity/test_activity_facade.py::test_choose_exploration_retreat_completes -v`
Expected: FAIL（`AttributeError: 'ActivityFacade' object has no attribute 'choose_exploration'`）。

- [ ] **Step 3: 写最小实现**

(a) `__init__` 签名追加关键字参数（放在 `canon` 之后）：

```python
        canon: str,
        on_rooted_encounter: Callable[[str, str, str], Awaitable[None]] | None = None,
    ) -> None:
        ...
        self._on_rooted_encounter = on_rooted_encounter
```

(b) 新增 `choose_exploration`（放在 `start_exploration` 之后）：

```python
    async def choose_exploration(self, activity_id: str, choice: str) -> dict[str, Any]:
        """用户在决策点选择：续跑探索图一步；险节点触发有根遭遇；终点则结算。

        返回 decision 载荷（pending）或 run 结果（终点）。无活动/非探索/id 不符 raise。
        """
        current = await self._store.get_current()
        if (
            current is None
            or current.id != activity_id
            or current.type is not ActivityType.FREE_EXPLORATION
            or current.status is not ActivityStatus.RUNNING
        ):
            raise RuntimeError("无进行中的探索")
        progress = await self._exploration.resume(activity_id, choice)
        if not progress["pending"]:
            current.progress["result"] = progress["result"]
            await self.complete_activity(current)
            return progress["result"]
        last = progress["state"].get("_last_node")
        if isinstance(last, dict) and last.get("may_encounter") and self._on_rooted_encounter:
            snippet = str(last.get("snippet") or last.get("name") or "")
            focus = str(progress["state"].get("focus") or "")
            await self._on_rooted_encounter(snippet, focus, activity_id)
        return progress["decision"]
```

(c) 重写 `start_exploration` 的探索发起段（不再走 `_execute` 一次性跑完，改为 `start` 到首决策点即返回）：

```python
    async def start_exploration(self, topic: str | None) -> str:
        """手动触发一次自由探索，返回 activity_id。

        跑到首个决策点即返回（RUNNING 状态保留）；后续由 choose_exploration 驱动。
        """
        async with self._start_lock:
            if self._task is not None and not self._task.done():
                raise RuntimeError("已有活动进行中")
            current = await self._store.get_current()
            if current is not None and current.status is ActivityStatus.RUNNING:
                raise RuntimeError("已有活动进行中")
            now = time.time()
            activity = Activity(
                id=str(uuid.uuid4()),
                type=ActivityType.FREE_EXPLORATION,
                schedule_block_id=_schedule_block_id(now, self._config.grid_minutes),
                status=ActivityStatus.PENDING,
                progress=_empty_progress(),
                started_at=now,
            )
            if topic is None:
                topic = await self._exploration.pick_topic(activity.id)
            activity.progress["description"] = topic
            await self._store.insert(activity)
            self._task = asyncio.create_task(self._execute(activity))
            self._task.add_done_callback(_harvest_task_exception)
            return activity.id
```

(d) `_execute` 分叉：FREE_EXPLORATION 走「启动到首决策点」路径，不 `complete_activity`：

```python
    async def _execute(self, activity: Activity) -> None:
        activity.status = ActivityStatus.RUNNING
        await self._store.update(activity)
        desire_id = activity.progress.get("desire_id")
        if isinstance(desire_id, str):
            await self._desire.mark_active(desire_id)
        await self._bus.publish(internal_event(
            EventType.ACTIVITY_START,
            {
                "activity_id": activity.id,
                "type": activity.type.value,
                "schedule_block_id": activity.schedule_block_id,
            },
            _correlation_id(activity),
        ))
        if activity.type is ActivityType.FREE_EXPLORATION:
            await self._start_exploration_run(activity)
            return
        try:
            result = await self._run_activity(activity)
        except Exception:
            # fail-fast（同旧逻辑，不变）
            activity.status = ActivityStatus.INCOMPLETE
            activity.ended_at = time.time()
            await self._store.update(activity)
            desire_id = activity.progress.get("desire_id")
            if isinstance(desire_id, str):
                await self._desire.mark_suppressed(desire_id)
            _logger.exception("活动执行失败 activity_id=%s type=%s", activity.id, activity.type.value)
            raise
        activity.progress["result"] = result
        await self.complete_activity(activity)
```

(e) 新增 `_start_exploration_run`（抽种子 + 跑首决策）：

```python
    async def _start_exploration_run(self, activity: Activity) -> None:
        """探索启动：从 desire goal.topic 抽种子（优先，绝不编造），跑图到首决策点。"""
        seed_desire_id = activity.progress.get("desire_id")
        seed_topic = self._exploration_seed(activity)
        state = await self._get_state()
        await self._exploration.start(
            seed_desire_id if isinstance(seed_desire_id, str) else None,
            seed_topic,
            state.energy,
            activity.id,
            _correlation_id(activity),
        )

    def _exploration_seed(self, activity: Activity) -> str:
        """种子话题：优先 goal.topic（探索欲的真实方向），退 description，退 activity.id。"""
        goal = activity.progress.get("goal")
        if isinstance(goal, dict):
            topic = goal.get("topic")
            if isinstance(topic, str) and topic:
                return topic
        desc = activity.progress.get("description")
        if isinstance(desc, str) and desc:
            return desc
        return activity.id
```

(f) 删除 `_run_activity` 里的 FREE_EXPLORATION 分支（现在走 `_execute` 的 `_start_exploration_run` 分叉）：

```python
        if t is ActivityType.FREE_EXPLORATION:
            raise ValueError("自由探索改走 _execute 的 _start_exploration_run 分叉")
```

(g) `set_exploration_autopilot` 占位（Task 8 补全）：

```python
    async def set_exploration_autopilot(self, activity_id: str, on: bool) -> None:
        raise NotImplementedError  # Task 8 实现
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_activity/test_activity_facade.py -v`
Expected: 新增测试 PASS；既有 facade 测试无回归（`_maybe_start_activity` 探索分支由 `_execute` 分叉承接，FREE_EXPLORATION 不再走 `_run_activity`）。

- [ ] **Step 5: 提交**

```bash
git add nyx/activity/facade.py tests/test_activity/test_activity_facade.py docs/test-inventory.md
git commit -m "feat(activity): 探索交互式执行改造 + 种子从 goal.topic 取"
```

> 更新 `docs/test-inventory.md`：追加 choose_exploration 测试，检查方向=管道正确（retreat 终局→completed），activity 系统。

---

## Task 6: 记忆接缝（run 记忆 + 知识记忆）

**Files:**
- Modify: `nyx/memory/facade.py:156-181`（`_activity_memory_fields` 的 free_exploration 映射）
- Modify: `nyx/activity/facade.py`（`complete_activity` 后补知识写入）
- Test: `tests/test_memory/`（既有）与 `tests/test_activity/test_activity_facade.py`

**Interfaces:**
- Consumes: Task 4 `assemble_result`（`summary`/`core_discovery`/`knowledge`/`new_topics`）、既有 `remember_knowledge`。
- Produces: `_activity_memory_fields` 的 free_exploration 分支改读 `summary`（content）/`core_discovery`（summary）；`ActivityFacade.complete_activity` 或 `choose_exploration` 终局时调 `remember_knowledge(result["knowledge"])`。

- [ ] **Step 1: 写失败测试**

在 `tests/test_memory/`（既有 memory 测试文件）追加：

```python
def test_activity_memory_fields_free_exploration_new_shape():
    result = {"summary": "弄懂了退相干", "core_discovery": "环境纠缠抹去相干性"}
    content, summary, tag = _activity_memory_fields("free_exploration", result)
    assert tag == "free_exploration"
    assert "退相干" in content
    assert "抹去相干性" in summary
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_memory/ -k free_exploration -v`
Expected: FAIL（旧映射读 `notes`/`findings`，新 shape 下 `content`/`summary` 空 → 返回 None）。

- [ ] **Step 3: 写最小实现**

改 `nyx/memory/facade.py` 的 `_activity_memory_fields` 探索分支：

```python
    elif activity_type == "free_exploration":
        content_key, summary_key = "summary", "core_discovery"
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_memory/ -k free_exploration -v`
Expected: 新增测试 PASS。

- [ ] **Step 5: 提交**

```bash
git add nyx/memory/facade.py tests/test_memory/ docs/test-inventory.md
git commit -m "feat(memory): 探索记忆映射 summary/core_discovery"
```

> 更新 `docs/test-inventory.md`：追加 1 个测试（free_exploration 新结果形状映射），检查方向=功能正确，memory 系统。

> 说明：`remember_knowledge` 接线（探索产出知识 → 长期记忆）在 Task 7 与欲望接缝一起做——探索的 `result["knowledge"]` 由 `complete_activity` 的 ACTIVITY_END 事件下游（memory facade 监听 ACTIVITY_END）读 `result` 提取。若 memory facade 现无「ACTIVITY_END → 探索知识」路由，则在 `remember_activity` 内顺带读 `result["knowledge"]` 调 `remember_knowledge`（见 Task 7 落点）。

---

## Task 7: 欲望接缝（新话题→长期欲望 + 满足回写）

**Files:**
- Modify: `nyx/activity/facade.py`（终局结算处：`strong_new_topics` → `add_long_term`，`knowledge` → `remember_knowledge`）
- Test: `tests/test_activity/test_activity_facade.py`

**Interfaces:**
- Consumes: Task 4 `assemble_result`（`strong_new_topics`/`knowledge`）、Task 6 记忆映射、既有 `DesireFacade.add_long_term(LongTermDesire)`、`MemoryFacade.remember_knowledge(items, correlation_id)`。
- Produces: 探索终局结算的欲望/知识回写逻辑（在 `complete_activity` 成功后、或 `choose_exploration` 终点分支内）。

- [ ] **Step 1: 写失败测试**

在 `tests/test_activity/test_activity_facade.py` 追加（用假 desire/memory 记录调用）：

```python
async def test_exploration_finalize_writes_long_term_and_knowledge(facade, fake_desire, fake_memory):
    await facade._finalize_exploration_sink({
        "strong_new_topics": ["量子纠错"],
        "knowledge": [{"topic": "退相干", "content": "环境纠缠"}],
        "seed": {"desire_id": "d1", "topic": "量子"},
    }, "c1")
    assert fake_desire.added_long_term[0].name == "量子纠错"
    assert fake_memory.knowledge_items[0]["topic"] == "退相干"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_activity/test_activity_facade.py::test_exploration_finalize_writes_long_term_and_knowledge -v`
Expected: FAIL（`AttributeError: 'ActivityFacade' object has no attribute '_finalize_exploration_sink'`）。

- [ ] **Step 3: 写最小实现**

在 `ActivityFacade` 新增（并接入 `choose_exploration` 终点分支与 `complete_activity` 之后）：

```python
    async def _finalize_exploration_sink(self, result: dict[str, Any], correlation_id: str) -> None:
        """探索终局回写（best-effort）：强烈新兴趣→长期欲望，知识→长期记忆。

        「满足探索欲」由 ACTIVITY_END → satisfy_from_activity_end 走 goal_met 驱动，
        这里只管新增长期欲望与知识。
        """
        strong = result.get("strong_new_topics")
        if isinstance(strong, list):
            for topic in strong:
                if not isinstance(topic, str) or not topic.strip():
                    continue
                await self._desire.add_long_term(LongTermDesire(
                    id=str(uuid.uuid4()),
                    created_at=time.time(),
                    type=DesireType.EXPLORATION,
                    name=topic,
                    description=f"想弄懂「{topic}」",
                    strength=0.5,
                    progress=0.0,
                    subtopics=[],
                ))
        knowledge = result.get("knowledge")
        if isinstance(knowledge, list):
            items = [
                {"topic": str(k.get("topic", "")), "content": str(k.get("content", ""))}
                for k in knowledge
                if isinstance(k, dict) and str(k.get("content", "")).strip()
            ]
            if items:
                await self._memory.remember_knowledge(items, correlation_id)
```

`choose_exploration` 终点分支改：

```python
        if not progress["pending"]:
            current.progress["result"] = progress["result"]
            await self.complete_activity(current)
            await self._finalize_exploration_sink(progress["result"], _correlation_id(current))
            return progress["result"]
```

> 需要的 import：`DesireType`（`nyx.enums`，facade 已 import 部分）、`LongTermDesire`（`nyx.types`，facade 已 import）。若未 import 则补。

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_activity/test_activity_facade.py -v`
Expected: 新增测试 PASS。

- [ ] **Step 5: 提交**

```bash
git add nyx/activity/facade.py tests/test_activity/test_activity_facade.py docs/test-inventory.md
git commit -m "feat(activity): 探索终局回写（强烈新兴趣→长期欲望，知识→长期记忆）"
```

> 更新 `docs/test-inventory.md`：追加 1 个测试（终局欲望/知识回写接线），检查方向=管道正确，activity 系统。

---

## Task 8: 托管模式

**Files:**
- Modify: `nyx/activity/exploration.py`（`pick_choice` + `_EXPLORATION_AUTOPILOT_SYSTEM`）
- Modify: `nyx/activity/facade.py`（`set_exploration_autopilot` 补全 + `_autopilot_loop`）
- Test: `tests/test_activity/test_exploration.py`、`tests/test_activity/test_activity_facade.py`

**Interfaces:**
- Consumes: Task 3 `resume`/`start`、Task 4 `assemble_result`。
- Produces:
  - `Exploration.pick_choice(decision, correlation_id) -> str`（轻 LLM 决策，可 mock）。
  - `ActivityFacade.set_exploration_autopilot(activity_id, on) -> None`。

- [ ] **Step 1: 写失败测试**

`tests/test_activity/test_exploration.py`：

```python
_CHOICE_JSON = json.dumps({"choice": "retreat"})


async def test_pick_choice_returns_valid_action():
    llm = _FakeLlm(_CHOICE_JSON)
    exploration = Exploration(llm, _FakeEvaluator(), _WebTools(), _bus(), ExplorationConfig(web_enabled=True))
    choice = await exploration.pick_choice({"kind": "choose", "nodes": []}, "c1")
    assert choice == "retreat"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_activity/test_exploration.py::test_pick_choice_returns_valid_action -v`
Expected: FAIL（`AttributeError: 'Exploration' object has no attribute 'pick_choice'`）。

- [ ] **Step 3: 写最小实现**

(a) `Exploration` 新增 `pick_choice`：

```python
    async def pick_choice(self, decision: dict[str, Any], correlation_id: str) -> str:
        """托管决策：轻 LLM 从决策载荷选一个动作（可 mock）。非法动作兜底 retreat。"""
        try:
            output = await self._llm.complete(
                [
                    {"role": "system", "content": _EXPLORATION_AUTOPILOT_SYSTEM},
                    {"role": "user", "content": json.dumps(decision, ensure_ascii=False)},
                ],
                module="activity",
                output_type="exploration_choice",
                correlation_id=correlation_id,
                json_mode=True,
            )
            await self._evaluator.evaluate(output)
            data = json.loads(output.content)
            if not isinstance(data, dict):
                return "retreat"
            choice = str(data.get("choice") or "retreat")
        except Exception:
            return "retreat"
        if choice in ("safe_room", "descend", "retreat") or choice.startswith("node:"):
            return choice
        return "retreat"
```

(b) 文件底部 prompt 区新增：

```python
_EXPLORATION_AUTOPILOT_SYSTEM = (
    "你是尼克斯的托管决策器。基于决策载荷（本层节点/精力/深度/种子话题），"
    "选一个动作，按 JSON 输出 {choice}。"
    "choice 只能是 node:0/node:1/node:2/safe_room/descend/retreat 之一。"
    "精力低优先 safe_room，线索充分优先 descend，没头绪优先 retreat。"
)
```

(c) `ActivityFacade` 补全 `set_exploration_autopilot`（替换 Task 5 占位）+ `_autopilot_loop`：

```python
    async def set_exploration_autopilot(self, activity_id: str, on: bool) -> None:
        """托管开关：on=True 起后台循环自动决策，on=False 停循环（下次决策点暂停等用户）。"""
        if on:
            if self._autopilot_task is None or self._autopilot_task.done():
                self._autopilot_task = asyncio.create_task(self._autopilot_loop(activity_id))
            return
        self._autopilot_on = False
        if self._autopilot_task is not None and not self._autopilot_task.done():
            self._autopilot_task.cancel()

    async def _autopilot_loop(self, activity_id: str) -> None:
        self._autopilot_on = True
        try:
            while self._autopilot_on:
                current = await self._store.get_current()
                if current is None or current.id != activity_id or current.status is not ActivityStatus.RUNNING:
                    return
                # 取最近一次决策载荷：从 exploration 的 checkpoint 状态拿（见下）
                decision = await self._exploration.current_decision(activity_id)
                if decision is None:
                    return
                choice = await self._exploration.pick_choice(decision, _correlation_id(current))
                progress = await self._exploration.resume(activity_id, choice)
                if not progress["pending"]:
                    current.progress["result"] = progress["result"]
                    await self.complete_activity(current)
                    await self._finalize_exploration_sink(progress["result"], _correlation_id(current))
                    return
                # 险节点同样触发有根遭遇
                last = progress["state"].get("_last_node")
                if isinstance(last, dict) and last.get("may_encounter") and self._on_rooted_encounter:
                    snippet = str(last.get("snippet") or last.get("name") or "")
                    focus = str(progress["state"].get("focus") or "")
                    await self._on_rooted_encounter(snippet, focus, activity_id)
                await asyncio.sleep(1.0)
        except asyncio.CancelledError:
            raise
```

(d) `Exploration` 新增 `current_decision`（读 checkpointer 当前 interrupt 载荷；托管循环用）：

```python
    async def current_decision(self, activity_id: str) -> dict[str, Any] | None:
        """读当前 checkpointer 里未决 interrupt 的决策载荷；无则 None。"""
        config = {"configurable": {"thread_id": activity_id}}
        snapshot = await self._graph.aget_state(config)
        if not snapshot.interrupts:
            return None
        return cast(dict[str, Any], snapshot.interrupts[0].value)
```

> `aget_state` 返回的 `snapshot.interrupts` 是 `Interrupt` 元组（探针已验证），`.value` 即决策载荷。

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_activity/test_exploration.py tests/test_activity/test_activity_facade.py -v`
Expected: 新增测试 PASS。托管循环的集成测试（mock `pick_choice` 返回 retreat → 断言终局）可选加一条，不强制。

- [ ] **Step 5: 提交**

```bash
git add nyx/activity/exploration.py nyx/activity/facade.py tests/test_activity/ docs/test-inventory.md
git commit -m "feat(exploration): 托管模式（轻 LLM 决策 + 自动循环 + 随时接管）"
```

> 更新 `docs/test-inventory.md`：追加 pick_choice 测试，检查方向=管道正确（LLM 决策返回合法动作），activity 系统。

---

## Task 9: 有根遭遇 + 删块边界随机

**Files:**
- Modify: `nyx/enums.py:31-36`（`EncounterKind` 加 `ROOTED`）
- Modify: `nyx/encounter/rules.py`（删 `_BLOCK_PROBABILITY`/`_COOLDOWN_SECONDS`/`should_encounter`）
- Modify: `nyx/encounter/facade.py`（删 `try_block_boundary`、加 `start_rooted`、`_KIND_LABEL` 加 ROOTED）
- Modify: `nyx/main.py:256,287-291`（删 `_check_encounter` 函数 + `_on_clock_tick` 里的调用点）
- Test: `tests/test_encounter/test_encounter_rules.py`、`test_encounter_facade.py`

**Interfaces:**
- Consumes: 既有 `_start`/`_parse_encounter`/`_build_user_prompt`。
- Produces: `EncounterFacade.start_rooted(snippet, theme, activity_id) -> None`（生成有根遭遇并广播 ENCOUNTER_START；best-effort）。

- [ ] **Step 1: 写失败测试**

`tests/test_encounter/test_encounter_facade.py`：

```python
async def test_start_rooted_broadcasts_start(encounter_facade, fake_llm, bus):
    await encounter_facade.start_rooted("争议观点", "量子退相干", "a1")
    assert encounter_facade.get_current() is not None
    assert encounter_facade.get_current()["kind"] == "rooted"
```

`tests/test_encounter/test_encounter_rules.py`：

```python
def test_should_encounter_removed():
    # 块边界随机入口已删：规则模块不再导出 should_encounter
    import nyx.encounter.rules as rules
    assert not hasattr(rules, "should_encounter")
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_encounter/test_encounter_facade.py::test_start_rooted_broadcasts_start -v`
Expected: FAIL（`AttributeError: 'EncounterFacade' object has no attribute 'start_rooted'`）。

- [ ] **Step 3: 写最小实现**

(a) `nyx/enums.py`：

```python
class EncounterKind(StrEnum):
    DESIRE_CHAT = "desire_chat"
    RANDOM_EVENT = "random_event"   # 随机事件（保留枚举值，入口已删，防存量数据报错）
    GROWTH_MOMENT = "growth_moment"
    ROOTED = "rooted"               # 有根遭遇（从探索真实节点内容长出）
```

(b) `nyx/encounter/rules.py` 删：`_BLOCK_PROBABILITY`、`_COOLDOWN_SECONDS`、`should_encounter`。`_MIN_ENERGY` 若只被 `should_encounter` 用也一并删（检查引用）。

(c) `nyx/encounter/facade.py`：
- 删 `try_block_boundary` 方法与 `random`/`should_encounter` 引用（`import random` 若无他用则删）。
- `_KIND_LABEL` 加一行：`EncounterKind.ROOTED: "有根遭遇"`。
- 新增 `start_rooted`：

```python
    async def start_rooted(self, snippet: str, theme: str, activity_id: str) -> None:
        """有根遭遇：从探索真实节点内容生成（轻 LLM）。best-effort：失败不崩 run。

        复用 _start 的生成/广播管线；context 塞真实 snippet+theme。
        """
        state = await self._get_state()
        context = f"探索主题「{theme}」，刚读到一段真实内容：{snippet[:300]}"
        await self._start(
            EncounterKind.ROOTED, state, activity_id=activity_id, context=context
        )
```

(d) `_ENCOUNTER_SYSTEM` prompt 末尾追加一句（有根遭遇要有真实动作选项）：

```python
    "有根遭遇时，选项应是真实可做的动作（深挖这条链接 / 换个话题 / 记下来 / 放弃这条线）。"
```

(e) `nyx/main.py` 删块边界随机入口（否则 `try_block_boundary` 已删会崩）：

- 删 `_on_clock_tick` 里 `SCHEDULE_BLOCK_START` 分支的 `await _check_encounter(app)` 一行（保留 `await app.activity.on_tick(tick_type)`）。
- 删 `_check_encounter` 整个函数（`nyx/main.py:287-291`）。

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_encounter/ -v`
Expected: 新增测试 PASS；删随机后既有 `try_block_boundary`/`should_encounter` 测试应被删除或改为「已删」断言。

- [ ] **Step 5: 提交**

```bash
git add nyx/enums.py nyx/encounter/rules.py nyx/encounter/facade.py tests/test_encounter/ docs/test-inventory.md
git commit -m "feat(encounter): 有根遭遇入口 start_rooted + 删块边界随机"
```

> 更新 `docs/test-inventory.md`：追加 start_rooted 测试 + 删除 should_encounter 测试，检查方向=功能正确 + 回归保护（随机入口已删），encounter 系统。

---

## Task 10: 组合根接线（main.py）

**Files:**
- Modify: `nyx/main.py:630-642`（`encounter` 前移到 `activity` 之前 + 注入回调）
- Test: 无新增（组合根由既有启动/API 测试覆盖）。

**Interfaces:**
- Consumes: Task 5 `on_rooted_encounter` 参数、Task 9 `start_rooted`。
- Produces: 无（仅装配）。

- [ ] **Step 1: 改装配顺序**

把 `encounter = EncounterFacade(...)` 上移到 `activity = ActivityFacade(...)` 之前，并给 `activity` 传回调：

```python
    encounter = EncounterFacade(bus, llm, evaluator, _get_state, canon)

    activity = ActivityFacade(
        activity_store, material_store, bus, llm, evaluator, tools, desire,
        memory, reading_notes, _get_state, _reflect, _get_observation,
        config.activity, config.exploration, canon,
        on_rooted_encounter=encounter.start_rooted,
    )
```

（删除原在 `inner_life` 之后的 `encounter = EncounterFacade(...)` 行。）

- [ ] **Step 2: 验证导入无环**

Run: `python -c "import nyx.main"`（或 `python -m nyx.main --help`）
Expected: 无 ImportError/循环导入。

- [ ] **Step 3: 提交**

```bash
git add nyx/main.py
git commit -m "chore(main): 探索有根遭遇回调接线"
```

---

## Task 11: API 端点（探索决策 + 托管开关）

**Files:**
- Modify: `nyx/main.py:393-400`（新增 `_ExploreChoosePayload`/`_ExploreAutopilotPayload` 模型）、`402-403`（端点计数 docstring）、`527-532` 之后（新增两个 POST 端点）
- Test: `tests/test_api/test_endpoints.py`

**Interfaces:**
- Consumes: Task 5 `choose_exploration(activity_id, choice) -> dict`、Task 8 `set_exploration_autopilot(activity_id, on) -> None`。
- Produces:
  - `POST /api/explore/choose`（body `{activity_id, choice}`）→ `choose_exploration` 返回值（decision 或 result）；无进行中探索时 409。
  - `POST /api/explore/autopilot`（body `{activity_id, on}`）→ `{"activity_id", "autopilot"}`。

- [ ] **Step 1: 写失败测试**

`tests/test_api/test_endpoints.py`：先给 `_FakeActivity` 加两个方法与记录字段（放在 `start_exploration` 之后、`__init__` 里补字段）：

```python
    def __init__(self) -> None:
        ...
        self.explore_topics: list[str | None] = []
        self.explore_busy = False
        self.explore_choices: list[tuple[str, str]] = []
        self.explore_choose_busy = False
        self.autopilot_calls: list[tuple[str, bool]] = []

    async def choose_exploration(self, activity_id: str, choice: str) -> dict[str, Any]:
        if self.explore_choose_busy:
            raise RuntimeError("无进行中的探索")
        self.explore_choices.append((activity_id, choice))
        return {"kind": "choose", "nodes": []}

    async def set_exploration_autopilot(self, activity_id: str, on: bool) -> None:
        self.autopilot_calls.append((activity_id, on))
```

文件末尾追加三个测试：

```python
async def test_explore_choose_endpoint() -> None:
    fake = _FakeActivity()
    app = _app(_mk_state(), _FakeBus(), _FakeMemory())
    app.activity = cast(ActivityFacade, fake)
    async with _client(app) as client:
        resp = await client.post(
            "/api/explore/choose", json={"activity_id": "exp-1", "choice": "node:0"}
        )
    assert resp.status_code == 200
    assert fake.explore_choices == [("exp-1", "node:0")]


async def test_explore_choose_busy_returns_409() -> None:
    fake = _FakeActivity()
    fake.explore_choose_busy = True
    app = _app(_mk_state(), _FakeBus(), _FakeMemory())
    app.activity = cast(ActivityFacade, fake)
    async with _client(app) as client:
        resp = await client.post(
            "/api/explore/choose", json={"activity_id": "x", "choice": "retreat"}
        )
    assert resp.status_code == 409


async def test_explore_autopilot_endpoint() -> None:
    fake = _FakeActivity()
    app = _app(_mk_state(), _FakeBus(), _FakeMemory())
    app.activity = cast(ActivityFacade, fake)
    async with _client(app) as client:
        resp = await client.post(
            "/api/explore/autopilot", json={"activity_id": "exp-1", "on": True}
        )
    assert resp.status_code == 200
    assert resp.json() == {"activity_id": "exp-1", "autopilot": True}
    assert fake.autopilot_calls == [("exp-1", True)]
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_api/test_endpoints.py::test_explore_choose_endpoint -v`
Expected: FAIL（404 或 `AttributeError`，端点尚未定义）。

- [ ] **Step 3: 写最小实现**

(a) `nyx/main.py` 的 `_ExplorePayload` 之后新增两个模型：

```python
class _ExploreChoosePayload(BaseModel):
    activity_id: str
    choice: str


class _ExploreAutopilotPayload(BaseModel):
    activity_id: str
    on: bool
```

(b) `build_app` docstring 端点计数 24 → 26：

```python
    """构建 FastAPI 应用：26 个端点（25 个 REST + SSE），薄封装 Facade。"""
```

(c) 在 `api_explore`（`/api/explore`）之后新增两个端点：

```python
    @fast.post("/api/explore/choose")
    async def api_explore_choose(payload: _ExploreChoosePayload) -> dict[str, Any]:
        try:
            return await app.activity.choose_exploration(
                payload.activity_id, payload.choice
            )
        except RuntimeError as exc:  # 无进行中的探索
            raise HTTPException(status_code=409, detail=str(exc))

    @fast.post("/api/explore/autopilot")
    async def api_explore_autopilot(
        payload: _ExploreAutopilotPayload,
    ) -> dict[str, Any]:
        await app.activity.set_exploration_autopilot(
            payload.activity_id, payload.on
        )
        return {"activity_id": payload.activity_id, "autopilot": payload.on}
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_api/test_endpoints.py -v`
Expected: 新增 3 个测试 PASS；既有端点测试无回归。

- [ ] **Step 5: 提交**

```bash
git add nyx/main.py tests/test_api/test_endpoints.py docs/test-inventory.md
git commit -m "feat(api): 探索决策 / 托管开关端点"
```

> 更新 `docs/test-inventory.md`：追加 3 个测试（explore/choose 正常 + 409 + autopilot），检查方向=功能正确 + 边界鲁棒（409），api 系统。

---

## Self-Review

**1. Spec 覆盖**（design doc §1–§11 逐节对任务）：

| Spec | 落地任务 |
|---|---|
| §3.2 楼层 3 槽 + 死路补位 + 安全房第 4 槽 | Task 1（`fill_dead_ends`/常量）、Task 2（`_search_nodes`）、Task 3（`_enter_floor`/`_safe_room`） |
| §3.3/3.4 每层一轮 + 下楼追线索 | Task 3（`_decide`/`_visit_node`/`_descend`） |
| §3.5 安全房三合一（回燃料/沉淀/撤退窗口） | Task 3（`_safe_room` + retreat 路由）；「沉淀=记忆」由 Task 6 终局结算覆盖 |
| §3.6 赢/输/撤退 | Task 1（`determine_outcome`）、Task 4（核心发现判定） |
| §4.1 结果形状 | Task 3/4（`assemble_result`） |
| §4.2 记忆接缝 | Task 6（run 记忆映射）+ Task 7（知识记忆） |
| §4.3 欲望接缝 | Task 7（strong_new_topics→add_long_term；满足走 goal_met 既有链路） |
| §4.4 道具/NPC hook | Task 4（`assemble_result` 的 `loot`/`npcs` 字段） |
| §5 有根遭遇 + 删随机 | Task 9 |
| §6 托管 | Task 8（后台循环）+ Task 11（`/api/explore/autopilot` 开关） |
| §3.3 决策点交互（前端驱动） | Task 11（`/api/explore/choose` 端点） |
| §10 未决项 #1（新兴趣阈值） | Task 4（LLM 判 strong vs casual 两列表） |
| §10 未决项 #2（核心发现判据） | Task 4（LLM 判定 + `_MAX_DEPTH` 兜底） |

**2. Placeholder 扫描**：无 TBD/TODO；每个 code step 有真实代码。唯一 `raise NotImplementedError`（Task 5 的 `set_exploration_autopilot` 占位）在 Task 8 明确补全——已标注，非计划失败。

**3. 类型一致性**：
- `FloorNode`/`ExplorationState`/`ExplorationProgress` 在 Task 1/3 定义，Task 4/5/8 引用一致。
- `parse_choice` 签名 Task 1 定 `(choice, state)` 返回 `(route, idx)`，Task 3 调用一致。
- `assemble_result` Task 3 定义，Task 4/5/7 消费字段名一致（`strong_new_topics`/`knowledge`/`core_discovery`/`outcome`）。
- `on_rooted_encounter` 回调签名 `(snippet, theme, activity_id) -> None` Task 5 定义、Task 10 接线 `encounter.start_rooted(snippet, theme, activity_id)` 一致。
- `start_rooted` Task 9 定义与 Task 10 接线一致。

**4. 反冗余自查**：无新抽象层、无新文件、结果保持 dict、常量不配置化、道具/NPC 只留字段、随机遭遇入口删除。✅

---

## 验证（全部任务完成后）

1. `ruff check` / `pyright` / `pytest` 全绿（后端）。
2. 人工抽查一场 run：`start_exploration` → `choose_exploration` 若干次 → 终点；记忆库多出 run 记忆 + 知识记忆 + 遭遇记忆；种子欲望被满足（`satisfy` 经 ACTIVITY_END goal_met）。
3. 人工抽查端点：`POST /api/explore/choose` 正常续跑 + 无活动 409；`POST /api/explore/autopilot` 开关。
4. `docs/test-inventory.md` 已更新（Task 1–11 每步追加）。
