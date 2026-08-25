# 探索升级：联网探索 + 探索地图 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 Nyx 的「探索欲」直接走上网探索（联网为主通道、本地兜底），把探索过程可视化成一张游戏小地图（节点=网页），主界面独立入口 + 实时逐步推送 + 手动「出门探索」。

**Architecture:** 后端复用现有 LangGraph `Exploration` 链，只改动作轮转顺序（联网为主）+ 加节点记录（每访问网页/发搜索记一条 `{name,url,kind}` 并 publish 新事件 `EXPLORATION_STEP`）；探索触发门槛放宽（去精力门、频率 4h→1h）；新增 `POST /api/explore` 手动端点复用 `ActivityFacade._execute` 管线。前端新增 `explorationStore`（实时节点 + 心愿单，前端内存）+ `ExplorationMap` 组件（节点链 + 出门探索按钮 + 加节点），SSE 新事件经 `dispatchEvent` 分发点亮地图。

**Tech Stack:** Python 3.11 + LangGraph + FastAPI + aiosqlite（后端）；React 18 + TypeScript strict + Zustand + Vite（前端）。

**Spec:** `docs/design/exploration-map.md`（本计划实现的设计文档，逐节对应）

## Global Constraints

- Python 3.11+，所有函数签名完整类型标注；Facade/I/O 用 `async def`，纯函数同步。
- 命名：Python `snake_case` / `PascalCase`；TS 组件 `PascalCase`、文件 `camelCase.tsx`。
- 枚举用 `StrEnum`，值 = 名小写（`EXPLORATION_STEP = "exploration_step"`）。
- LLM 调用统一走 `LlmClient.complete(...)`，带 `module`/`output_type`/`correlation_id`，json 结果用 `json_mode=True`。
- 后端每个 Facade 方法测试 ≤ 5 断言；Mock LLM/tools/bus，不依赖真实网络/文件/桌面。
- **每次写测试后必须更新 `docs/test-inventory.md`**（追加系统/方向/阶段/检查点）。
- spec 内联代码必须与实现逐字追平（`02-config.md`、`14-activity.md`）。
- 反冗余：不新增抽象层；不新增设计文档未定义的新文件/类/配置项。
- 提交信息以 `Co-Authored-By: Claude <noreply@anthropic.com>` 结尾；仅用户说「提交」才 commit（本计划步骤里的 commit 是示意，执行时按用户节奏）。
- 后端质量门：`python -m ruff check nyx/ tests/` + `python -m pyright nyx/ tests/` + `python -m pytest -q` 全绿。前端：`cd frontend && npx tsc --noEmit` + `npx vitest run` 全绿。

---

### Task 1: 事件枚举 + 路由（EXPLORATION_STEP）

**Files:**
- Modify: `nyx/enums.py`（EventType 末尾加 `EXPLORATION_STEP`）
- Modify: `nyx/events/routing.py`（加 `EXPLORATION_STEP: []`）
- Test: `tests/test_types/test_enums.py`（EventType EXPECTED 集合加 `"exploration_step"`）

**Interfaces:**
- Consumes: 无（地基任务）。
- Produces: `EventType.EXPLORATION_STEP`（值 `"exploration_step"`），后续 Task 3 的 `Exploration._record_node` 用 `internal_event(EventType.EXPLORATION_STEP, {...})` 发布；Task 5 前端 `types/api.ts` 加 `"exploration_step"` 判别联合。

- [ ] **Step 1: 改失败测试（枚举穷举测试先加新值）**

在 `tests/test_types/test_enums.py` 的 `EXPECTED[EventType]` 集合末尾加 `"exploration_step"`（`activity_interrupted` 之后）：

```python
        "desire_satisfied", "desire_expired", "activity_start", "activity_end",
        "activity_interrupted", "exploration_step",
    },
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_types/test_enums.py::test_all_enums_exhaustive -v`
Expected: FAIL —— `{m.value for m in EventType}` 缺 `exploration_step`，与 EXPECTED 不相等。

- [ ] **Step 3: 实现枚举 + 路由**

`nyx/enums.py`，`ACTIVITY_INTERRUPTED` 之后追加：

```python
    ACTIVITY_INTERRUPTED = "activity_interrupted"  # 活动打断
    EXPLORATION_STEP = "exploration_step"          # 探索逐步进度（每节点一推，仅广播前端）
```

`nyx/events/routing.py`，`MEMORY_PROMOTED: []` 之后追加（与 `REFLECTION_DONE: []` 同为「仅广播前端、无内部消费者」）：

```python
    EventType.MEMORY_PROMOTED:     [],
    EventType.EXPLORATION_STEP:    [],   # 探索进度：仅广播前端地图，无后端消费者
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_types/test_enums.py tests/test_event/test_routing.py -v`
Expected: PASS（`test_routing_keys_are_all_event_types_except_clock_tick` 会自动覆盖：ROUTING 现在含 `EXPLORATION_STEP` 键，恰好补齐 `set(EventType) - {CLOCK_TICK}`）。

- [ ] **Step 5: Commit**

```bash
git add nyx/enums.py nyx/events/routing.py tests/test_types/test_enums.py
git commit -m "feat(activity): 新增 EXPLORATION_STEP 事件类型 + 广播路由

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 2: 探索门槛放宽（去精力门 + 频率 4h→1h）

**Files:**
- Modify: `nyx/activity/exploration.py`（`should_explore` 去掉 energy 参数与 `_FREE_EXPLORATION_ENERGY`）
- Modify: `nyx/activity/facade.py:536-542`（`should_explore` 调用去掉 `state.energy`）
- Modify: `nyx/config.py:73`（`rate_limit_hours: int = 4` → `1`）
- Modify: `config.yaml:39`（`rate_limit_hours: 4` → `1`）
- Test: `tests/test_activity/test_exploration.py`（3 个 `should_explore` 测试改写）
- Doc: `docs/specs/02-config.md`、`docs/tech-reference.md`、`docs/specs/14-activity.md`（`rate_limit_hours` 默认值 + `should_explore` 内联代码）

**Interfaces:**
- Consumes: `SECONDS_PER_HOUR`（`nyx/events/event.py`，已在 exploration.py import）。
- Produces: `should_explore(last_explored_at: float, rate_limit_hours: int, now: float) -> bool`（纯函数，仅频率判定）。Task 4 的手动端点不经过它（手动无视门槛），无依赖。

- [ ] **Step 1: 改失败测试**

`tests/test_activity/test_exploration.py` 的 `# ---- should_explore ----` 段，三个测试替换为两个（`test_should_explore_energy_too_low` 删除——精力门已移除，无 energy 入参）：

```python
def test_should_explore_rate_limited() -> None:
    # 频率未过（now - last < 1h*3600）→ False，与精力无关
    assert should_explore(1_000.0, 1, 1_000.0 + 3_599.0) is False


def test_should_explore_ok() -> None:
    # last=0（从未探索）+ 频率已过 → True；无 energy 入参（精力交给 build_schedule 兜底）
    assert should_explore(0.0, 1, 20_000.0) is True
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_activity/test_exploration.py -k should_explore -v`
Expected: FAIL —— 新签名 `should_explore(1_000.0, 1, ...)` 传 3 个位置参数，旧定义要 4 个（`energy` 仍在）。

- [ ] **Step 3: 实现签名放宽**

`nyx/activity/exploration.py`，删除常量并改写函数（`_FREE_EXPLORATION_ENERGY` 成为 orphan，一并删除）：

```python
def should_explore(last_explored_at: float, rate_limit_hours: int, now: float) -> bool:
    """自由探索升级门槛（纯函数）：仅频率上限。

    「探索欲」条件由调用方结构保证：READING 活动仅由 DesireType.EXPLORATION 映射而来
    （13 desire_to_activity），故调用方在 activity.type is READING 时才调本函数。
    精力不再单独卡：探索消耗 -30，精力不足由 build_schedule 的 REST 穿插兜底。
    """
    return now - last_explored_at >= rate_limit_hours * SECONDS_PER_HOUR
```

（同时删除文件顶部 `_FREE_EXPLORATION_ENERGY = 60.0` 常量行。）

`nyx/activity/facade.py:536-542`，调用去掉 `state.energy`：

```python
                    last = await self._store.get_last_exploration()
                    if should_explore(
                        last,
                        self._exploration_config.rate_limit_hours,
                        time.time(),
                    ):
```

`nyx/config.py:73`：`rate_limit_hours: int = 1`。
`config.yaml:39`：`rate_limit_hours: 1`。

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_activity/test_exploration.py -k should_explore -v`
Expected: PASS（2 个新测试 + `test_exploration.py` 其余探索测试不涉此函数）。

- [ ] **Step 5: 追平文档内联代码**

`docs/specs/14-activity.md`：删 `_FREE_EXPLORATION_ENERGY = 60.0` 行；`should_explore` 内联代码替换为无 energy 版本；`_maybe_start_activity` 内联的 `should_explore(last, self._exploration_config.rate_limit_hours, time.time())` 同步去掉 `state.energy`。
`docs/specs/02-config.md` + `docs/tech-reference.md`：`rate_limit_hours: 4` → `1`（含 `02-config.md` 内联 dataclass 默认值 `rate_limit_hours: int = 1`）。

- [ ] **Step 6: 更新 test-inventory 并 commit**

`docs/test-inventory.md` 追加/改写 `test_should_explore_rate_limited`、`test_should_explore_ok`（功能正确 / 边界鲁棒，探索系统，阶段=探索升级）；删除 `test_should_explore_energy_too_low` 条目。

```bash
git add nyx/activity/exploration.py nyx/activity/facade.py nyx/config.py config.yaml tests/test_activity/test_exploration.py docs/specs/02-config.md docs/tech-reference.md docs/specs/14-activity.md docs/test-inventory.md
git commit -m "feat(activity): 探索门槛放宽——去精力门、频率 4h→1h

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 3: 探索链联网主通道 + 节点记录 + EXPLORATION_STEP 发布

**Files:**
- Modify: `nyx/activity/exploration.py`（`ExplorationState` 加 `nodes`/`activity_id`；新增 `ExplorationNode` TypedDict；`Exploration.__init__` 加 `bus`；`run` 加 `activity_id` 并返回 `nodes`；`_actions` 联网优先；`_search_web` 记 search/web 节点 + 本地兜底；`_search_local` 记 search 节点；新增 `_record_node` + `_domain`）
- Modify: `nyx/activity/facade.py:257-259`（`Exploration(...)` 构造加 `bus`）、`facade.py:614-618`（`_run_activity` FREE_EXPLORATION 分支加 `activity_id`）
- Test: `tests/test_activity/test_exploration.py`（构造/状态/返回值/节点断言全量更新）

**Interfaces:**
- Consumes: `EventType.EXPLORATION_STEP`（Task 1）、`internal_event`（`nyx/events/event.py`）、`EventBus`（`nyx/events/bus.py`）。
- Produces: `ExplorationNode(TypedDict): {name: str, url: str, kind: str}`（`kind` 取 `"search"|"web"`）；`Exploration.run(seed: str, activity_id: str, correlation_id: str) -> dict[str, Any]` 返回 `{"findings": list[str], "notes": list[str], "nodes": list[ExplorationNode]}`；`Exploration.__init__(llm, evaluator, tools, bus, exploration_config)`。Task 4 的 `start_exploration` 依赖 `run` 新签名 + `Exploration` 新构造；Task 5 前端依赖 `nodes` 形状 `{name,url,kind}`。

- [ ] **Step 1: 改失败测试（构造/状态/返回值）**

`tests/test_activity/test_exploration.py`：

1. 顶部 import 增加 `EventType`、`internal_event`、`EventBus`：

```python
from nyx.enums import EventType
from nyx.events.bus import EventBus
from nyx.events.event import internal_event
```

2. 新增 `_FakeBus`（记录 publish 的事件）：

```python
class _FakeBus:
    def __init__(self) -> None:
        self.published: list[Any] = []

    async def publish(self, event: Any) -> None:
        self.published.append(event)
```

3. `_make_exploration` 加 `bus` 参数并传入：

```python
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
```

4. `_web_state()` 补齐 `nodes`/`activity_id`：

```python
def _web_state() -> ExplorationState:
    return {
        "seed": "x", "focus": "骑士", "findings": [], "notes": [],
        "nodes": [], "step": 0, "done": False,
        "activity_id": "a1", "correlation_id": "c",
    }
```

5. 两个 run 测试更新 `run` 入参 + 返回值键集合；`test_exploration_plan_non_dict_raises` 同步更新入参：

```python
    result = await expl.run("骑士团", "a1", "corr-1")
    assert set(result) == {"findings", "notes", "nodes"}
```

6. 新增「返回 nodes + 每节点 publish」测试（放 `# ---- Exploration.run ----` 段后）：

```python
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
```

7. 新增「联网为主 + 本地兜底」测试（`_WebTools` 的 `web_search` 返回空 → 走 `local_search`）：

```python
class _EmptyWebTools(_WebTools):
    async def call(self, name: str, args: dict[str, Any]) -> Any:
        self.calls.append((name, args))
        if name == "web_search":
            return []
        if name == "local_search":
            return ["本地兜底结果"]
        return "其他"


async def test_search_web_falls_back_to_local() -> None:
    tools = _EmptyWebTools()
    expl = _web_exploration(tools)
    state = _web_state()
    await expl._search_web(state)
    assert ("local_search", {"query": "骑士"}) in tools.calls
    assert any("本地兜底结果" in f for f in state["findings"])
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_activity/test_exploration.py -v`
Expected: FAIL —— `Exploration()` 少 `bus` 参数、`run()` 少 `activity_id`、`ExplorationState` 缺 `nodes`/`activity_id` 键、`set(result)` 缺 `nodes`。

- [ ] **Step 3: 实现探索链改造**

`nyx/activity/exploration.py` 全量改动：

```python
# 顶部 import 追加
from urllib.parse import urlparse

from nyx.enums import EventType
from nyx.events.bus import EventBus
from nyx.events.event import SECONDS_PER_HOUR, internal_event
```

```python
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
```

```python
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
        # 联网为主通道：web 开启时 search_web 是主搜索动作，search_local 作兜底（进 _search_web 内）
        if self._web_enabled:
            self._actions = ["search_web", "read", "write_note"]
        else:
            self._actions = ["search_local", "read", "write_note"]
        self._graph = self._build_graph()
```

```python
    async def run(self, seed: str, activity_id: str, correlation_id: str) -> dict[str, Any]:
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
```

`_search_local` 记 search 节点：

```python
    async def _search_local(self, state: ExplorationState) -> ExplorationState:
        await self._record_node(
            state, {"name": f"搜索：{state['focus']}", "url": "", "kind": "search"}
        )
        res = await self._tools.call("local_search", {"query": state["focus"]})
        state["findings"].extend(str(r) for r in res)
        return state
```

`_search_web` 记 search/web 节点 + 本地兜底：

```python
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
                url = first["url"]
                name = first.get("title") or _domain(url)
                await self._record_node(state, {"name": name, "url": url, "kind": "web"})
                # 顺手下第一条正文入书库；失败静默不崩探索
                try:
                    fetched = await self._tools.call("web_fetch", {"url": url})
                    state["findings"].append(f"已下载资料：{fetched}")
                except Exception:
                    pass
        return state
```

新增 `_record_node` + `_domain`（放在 `_finalize` 之后）：

```python
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


def _domain(url: str) -> str:
    """网页节点名兜底：title 缺失时用域名。"""
    host = urlparse(url).hostname
    return host or url
```

`nyx/activity/facade.py` 构造 + 调用点：

```python
        self._exploration = Exploration(
            llm, evaluator, tools, bus, exploration_config
        )
```

```python
        if t is ActivityType.FREE_EXPLORATION:
            return await self._exploration.run(
                seed=str(activity.progress.get("description") or activity.id),
                activity_id=activity.id,
                correlation_id=_correlation_id(activity),
            )
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_activity/test_exploration.py tests/test_activity/test_activity_facade.py -v`
Expected: PASS。注意 `test_exploration_run_no_web` 里 `_FakeTools` 的 `local_search` 返回 `["一条检索结果"]`（str，非 dict），`_search_web` 的 `isinstance(first, dict)` 守卫跳过 web 节点、不崩——`all(c[0] != "web_search" for c in tools.calls)` 断言不变。

- [ ] **Step 5: 追平文档 + test-inventory + commit**

`docs/specs/14-activity.md` 内联 `Exploration` 代码段追平（`ExplorationNode`/`ExplorationState`/`__init__` 加 bus/`run` 加 activity_id/`_actions`/`_search_web`/`_search_local`/`_record_node`/`_domain`），测试要点补 `nodes` 断言 + 兜底断言。
`docs/test-inventory.md` 追加 `test_exploration_run_returns_nodes_and_publishes_steps`、`test_search_web_falls_back_to_local`（功能正确，探索系统，探索升级阶段）。

```bash
git add nyx/activity/exploration.py nyx/activity/facade.py tests/test_activity/test_exploration.py docs/specs/14-activity.md docs/test-inventory.md
git commit -m "feat(activity): 探索链联网主通道 + 节点记录 + EXPLORATION_STEP 发布

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 4: 手动触发（start_exploration + pick_topic + POST /api/explore）

**Files:**
- Modify: `nyx/activity/exploration.py`（新增 `pick_topic(correlation_id)` + `_EXPLORATION_TOPIC_SYSTEM`）
- Modify: `nyx/activity/facade.py`（新增公开方法 `start_exploration(topic: str | None) -> str`）
- Modify: `nyx/main.py`（新增 `_ExplorePayload` + `POST /api/explore` 端点，注册到 `build_app`）
- Test: `tests/test_activity/test_activity_facade.py`（`start_exploration` 触发 + busy 拒绝）、`tests/test_api/test_endpoints.py`（端点）
- Doc: `docs/specs/18-api.md`（端点计数 15→16 或 20→21，视当前计数口径）

**Interfaces:**
- Consumes: `Exploration.run(seed, activity_id, correlation_id)`（Task 3）、`Exploration.pick_topic`（本任务新增）、`_execute`/`_start_lock`/`_schedule_block_id`/`Activity`/`uuid4`（facade 现有）。
- Produces: `ActivityFacade.start_exploration(topic: str | None) -> str`（返回 `activity_id`；busy 时 raise `RuntimeError`）；`POST /api/explore`（body `{topic?: string}` → `{"activity_id": string}`；busy → 409）。前端 Task 6 的 `postExplore` 依赖此契约。

- [ ] **Step 1: 改失败测试（facade 手动触发）**

`tests/test_activity/test_activity_facade.py`，在 `# ---- 恢复/续做 ----` 段前新增两个测试：

```python
async def test_start_exploration_returns_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    t0 = 1_000_000.0
    monkeypatch.setattr("nyx.activity.facade.time.time", lambda: t0)
    facade, store, bus, database = await _new_facade()
    try:
        async with _running(bus):
            activity_id = await facade.start_exploration("深海鱼")
            await _await_task(facade)
        assert isinstance(activity_id, str)
        acts = await store.list_schedule(0.0)
        assert [a.id for a in acts] == [activity_id]
        assert acts[0].type is ActivityType.FREE_EXPLORATION
        assert acts[0].progress["description"] == "深海鱼"
    finally:
        await database.conn.close()


async def test_start_exploration_busy_raises() -> None:
    facade, _store, _bus, database = await _new_facade(
        pending=[_desire("d1", DesireType.EXPLORATION)], energy=80.0
    )
    try:
        await facade._maybe_start_activity()
        assert facade._task is not None and not facade._task.done()
        with pytest.raises(RuntimeError):
            await facade.start_exploration("深海鱼")
        await facade._task
    finally:
        await database.conn.close()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_activity/test_activity_facade.py -k start_exploration -v`
Expected: FAIL —— `ActivityFacade` 无 `start_exploration` 属性。

- [ ] **Step 3: 实现 pick_topic + start_exploration**

`nyx/activity/exploration.py`，`_finalize` 之后加 `pick_topic`，文件尾加 prompt：

```python
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
        return str(plan.get("topic") or "有趣的新鲜事")
```

```python
_EXPLORATION_TOPIC_SYSTEM = (
    "你是尼克斯。给一个具体、可上网搜索的探索主题，按 JSON 输出 {topic}。"
)
```

`nyx/activity/facade.py`，在 `get_current` 之前的 `# ---- 读 ----` 段上方加公开方法（放在 `# ---- 生命周期 ----` 末尾、`read_material` 旁更贴合，见下）。实际插到 `read_material` 之前：

```python
    async def start_exploration(self, topic: str | None) -> str:
        """手动触发一次自由探索（无视欲望/频率门槛），返回 activity_id。

        复用 _execute 执行管线；不恢复 PAUSED（手动是全新出发）。
        已有活动在跑时 raise RuntimeError（端点转 409）。
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
                progress={"description": topic},
                started_at=now,
            )
            if topic is None:
                activity.progress["description"] = await self._exploration.pick_topic(
                    activity.id
                )
            await self._store.insert(activity)
            self._task = asyncio.create_task(self._execute(activity))
            self._task.add_done_callback(_harvest_task_exception)
            return activity.id
```

`nyx/main.py`：

1. `_AnnotationPayload` 之后加 payload：

```python
class _ExplorePayload(BaseModel):
    topic: str | None = None
```

2. `build_app` 的 `api_events`（`/api/events`）之前加端点：

```python
    @fast.post("/api/explore")
    async def api_explore(payload: _ExplorePayload) -> dict[str, str]:
        try:
            activity_id = await app.activity.start_exploration(payload.topic)
        except RuntimeError as exc:  # 已有活动在跑
            raise HTTPException(status_code=409, detail=str(exc))
        return {"activity_id": activity_id}
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_activity/test_activity_facade.py -k start_exploration tests/test_api/test_endpoints.py -v`
Expected: PASS。

- [ ] **Step 5: 端点测试**

`tests/test_api/test_endpoints.py`，`_FakeActivity` 加 `start_exploration`（记录 topic）+ busy 标志；新增 3 个端点测试：

```python
    def __init__(self) -> None:
        ...
        self.explore_topics: list[str | None] = []
        self.explore_busy = False

    async def start_exploration(self, topic: str | None) -> str:
        if self.explore_busy:
            raise RuntimeError("已有活动进行中")
        self.explore_topics.append(topic)
        return "exp-1"
```

```python
async def test_explore_endpoint_no_topic() -> None:
    fake = _FakeActivity()
    app = _app(_mk_state(), _FakeBus(), _FakeMemory())
    app.activity = cast(ActivityFacade, fake)
    async with _client(app) as client:
        resp = await client.post("/api/explore", json={})
    assert resp.status_code == 200
    assert resp.json() == {"activity_id": "exp-1"}
    assert fake.explore_topics == [None]


async def test_explore_endpoint_with_topic() -> None:
    fake = _FakeActivity()
    app = _app(_mk_state(), _FakeBus(), _FakeMemory())
    app.activity = cast(ActivityFacade, fake)
    async with _client(app) as client:
        resp = await client.post("/api/explore", json={"topic": "深海鱼"})
    assert resp.status_code == 200
    assert fake.explore_topics == ["深海鱼"]


async def test_explore_endpoint_busy_returns_409() -> None:
    fake = _FakeActivity()
    fake.explore_busy = True
    app = _app(_mk_state(), _FakeBus(), _FakeMemory())
    app.activity = cast(ActivityFacade, fake)
    async with _client(app) as client:
        resp = await client.post("/api/explore", json={"topic": "深海鱼"})
    assert resp.status_code == 409
```

（`_app()` 里的 `activity=cast(ActivityFacade, object())` 会在这三个测试里被 `app.activity = cast(ActivityFacade, fake)` 覆盖，与 `test_materials_endpoint` 同法。）

- [ ] **Step 6: 追平文档 + test-inventory + commit**

`docs/specs/18-api.md`：端点表加 `POST /api/explore`，计数对齐（REST 端点 +1）；内联代码加 `_ExplorePayload` + `api_explore`。`docs/design/exploration-map.md` 顶部状态改「已实现（部分）」待全量后更新（留待 Task 7 收尾）。
`docs/test-inventory.md` 追加 `test_start_exploration_returns_id`、`test_start_exploration_busy_raises`、`test_explore_endpoint_*`（功能正确/边界鲁棒，活动/API 系统，探索升级阶段）。

```bash
git add nyx/activity/exploration.py nyx/activity/facade.py nyx/main.py tests/test_activity/test_activity_facade.py tests/test_api/test_endpoints.py docs/specs/18-api.md docs/test-inventory.md
git commit -m "feat(api): 手动出门探索 POST /api/explore（含好奇驱动选题）

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 5: 前端类型 + SSE + dispatch

**Files:**
- Modify: `frontend/src/types/api.ts`（加 `ExplorationNode`/`ExplorationStepEvent`，SseEvent 联合加 `exploration_step`）
- Modify: `frontend/src/hooks/useSSE.ts`（EVENT_TYPES 加 `"exploration_step"`）
- Modify: `frontend/src/api/dispatch.ts`（加 `case "exploration_step"`）
- Test: `frontend/tests/sse.test.ts`（dispatch exploration_step → explorationStore）

**Interfaces:**
- Consumes: 后端 `exploration_step` content `{activity_id, node:{name,url,kind}}`（Task 3）。
- Produces: `ExplorationNode`、`ExplorationStepEvent`；`dispatchEvent` 新增分支调用 `useExplorationStore.getState().onStep(e)`（Task 6 实现 `onStep`）。

- [ ] **Step 1: 改失败测试（dispatch 分支）**

`frontend/tests/sse.test.ts`，`dispatchEvent` describe 的 `beforeEach` 加 `useExplorationStore.setState({ wishlist: [], liveNodes: [], activityId: null })`，并新增一个 it：

```typescript
  it("exploration_step → explorationStore.onStep（点亮地图节点）", () => {
    dispatchEvent({
      event: "exploration_step",
      event_id: "s1",
      correlation_id: "a1",
      activity_id: "a1",
      node: { name: "新闻", url: "https://example.com", kind: "web" },
    });

    expect(useExplorationStore.getState().liveNodes).toEqual([
      { name: "新闻", url: "https://example.com", kind: "web" },
    ]);
    expect(useExplorationStore.getState().activityId).toBe("a1");
  });
```

（顶部 import `useExplorationStore`。）

- [ ] **Step 2: 跑测试确认失败**

Run: `cd frontend && npx vitest run tests/sse.test.ts -t "exploration_step"`
Expected: FAIL —— `useExplorationStore` 未导出（Task 6 才建），且 `SseEvent` 联合无 `exploration_step`，`dispatchEvent` 传该对象报类型错。

- [ ] **Step 3: 实现类型 + SSE + dispatch**

`frontend/src/types/api.ts`，`ReflectionDoneEvent` 之后加：

```typescript
/** 探索地图节点：search = 搜索动作（url 空），web = 访问的网页。 */
export type ExplorationNode = {
  name: string;
  url: string;
  kind: "search" | "web";
};

/** 探索实时进度帧（exploration_step）：探索链每访问一个节点推一次。 */
export type ExplorationStepEvent = SseBase & {
  event: "exploration_step";
  activity_id: string;
  node: ExplorationNode;
};
```

`SseEvent` 联合末尾加 `| ExplorationStepEvent`：

```typescript
  | UserMessageEvent
  | EmotionUpdateEvent
  | ReflectionDoneEvent
  | ExplorationStepEvent
  | OpaqueEvent;
```

`frontend/src/hooks/useSSE.ts`，`EVENT_TYPES` 数组末尾加：

```typescript
  "activity_interrupted",
  "exploration_step",
];
```

`frontend/src/api/dispatch.ts`，顶部 import `useExplorationStore`，`reflection_done` case 之前加：

```typescript
    case "exploration_step":
      useExplorationStore.getState().onStep(e);
      return;
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd frontend && npx vitest run tests/sse.test.ts`
Expected: 此时 `onStep` 尚未实现（Task 6）→ 该测试仍 FAIL。为让本任务独立收尾，本任务与 Task 6 一并完成后再跑全量（见 Task 6 Step 4）。

- [ ] **Step 5: Commit（与 Task 6 合批或独立，见 Task 6）**

（本任务与 Task 6 共享一个「前端类型+store」变更集，建议 Task 6 完成后一起 commit，避免中间态不通过 tsc。）

---

### Task 6: explorationStore + client.postExplore

**Files:**
- Create: `frontend/src/stores/explorationStore.ts`
- Modify: `frontend/src/api/client.ts`（加 `postExplore`）
- Test: `frontend/tests/stores.test.ts`（store 行为）、`frontend/tests/api.test.ts`（postExplore）

**Interfaces:**
- Consumes: `postExplore` 调 `POST /api/explore`（Task 4 契约）；`onStep(e: ExplorationStepEvent)`（Task 5 类型）。
- Produces: `useExplorationStore`（`wishlist: string[]`、`liveNodes: ExplorationNode[]`、`activityId: string | null`、`addWish`、`removeWish`、`start`、`onStep`）。Task 7 组件消费这些。

- [ ] **Step 1: 改失败测试（store + client）**

`frontend/tests/stores.test.ts`，顶部 import `useExplorationStore` + `ExplorationNode`，新增 describe：

```typescript
describe("explorationStore", () => {
  beforeEach(() => {
    useExplorationStore.setState({ wishlist: [], liveNodes: [], activityId: null });
  });

  it("addWish / removeWish 心愿单增删", () => {
    useExplorationStore.getState().addWish("深海鱼");
    useExplorationStore.getState().addWish("发光生物");
    expect(useExplorationStore.getState().wishlist).toEqual(["深海鱼", "发光生物"]);

    useExplorationStore.getState().removeWish("深海鱼");
    expect(useExplorationStore.getState().wishlist).toEqual(["发光生物"]);
  });

  it("onStep：同 activity 追加，异 activity 重置", () => {
    const n1: ExplorationNode = { name: "搜索：深海鱼", url: "", kind: "search" };
    const n2: ExplorationNode = { name: "新闻", url: "https://e.com", kind: "web" };
    useExplorationStore.getState().onStep({ event: "exploration_step", event_id: "s1", correlation_id: "a1", activity_id: "a1", node: n1 });
    useExplorationStore.getState().onStep({ event: "exploration_step", event_id: "s2", correlation_id: "a1", activity_id: "a1", node: n2 });
    expect(useExplorationStore.getState().liveNodes).toEqual([n1, n2]);

    useExplorationStore.getState().onStep({ event: "exploration_step", event_id: "s3", correlation_id: "a2", activity_id: "a2", node: n1 });
    expect(useExplorationStore.getState().liveNodes).toEqual([n1]);
    expect(useExplorationStore.getState().activityId).toBe("a2");
  });

  it("start：POST /api/explore 后清空 liveNodes", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ activity_id: "exp-9" }));
    vi.stubGlobal("fetch", fetchMock);

    await useExplorationStore.getState().start();

    expect(fetchMock.mock.calls[0][0]).toBe("/api/explore");
    expect(fetchMock.mock.calls[0][1]).toMatchObject({ method: "POST" });
    expect(useExplorationStore.getState().activityId).toBe("exp-9");
    expect(useExplorationStore.getState().liveNodes).toEqual([]);
  });
});
```

`frontend/tests/api.test.ts`，加 `postExplore` 测试：

```typescript
  it("postExplore：无 topic 发空 body，有 topic 发 {topic}", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ activity_id: "e1" }));
    vi.stubGlobal("fetch", fetchMock);

    await postExplore();
    expect(fetchMock.mock.calls[0][0]).toBe("/api/explore");
    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual({});

    await postExplore("深海鱼");
    expect(JSON.parse(fetchMock.mock.calls[1][1].body)).toEqual({ topic: "深海鱼" });
  });
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd frontend && npx vitest run tests/stores.test.ts tests/api.test.ts`
Expected: FAIL —— `useExplorationStore`/`postExplore` 未定义。

- [ ] **Step 3: 实现 store + client**

`frontend/src/api/client.ts`，`getActivityResults` 之后加：

```typescript
export async function postExplore(
  topic?: string,
): Promise<{ activity_id: string }> {
  return request<{ activity_id: string }>(`${BASE_URL}/api/explore`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(topic !== undefined ? { topic } : {}),
  });
}
```

`frontend/src/stores/explorationStore.ts`（新建）：

```typescript
import { create } from "zustand";
import { postExplore } from "../api/client";
import type { ExplorationNode, ExplorationStepEvent } from "../types/api";

// 探索地图（design §4.1）：实时节点 + 心愿单。心愿单 MVP 只存前端内存，不落库。
type ExplorationStoreState = {
  wishlist: string[];                 // 待探索心愿（主题词）
  liveNodes: ExplorationNode[];       // 当前探索实时累积的节点（exploration_step 推送）
  activityId: string | null;          // 当前探索 activity_id
  addWish: (topic: string) => void;
  removeWish: (topic: string) => void;
  start: () => Promise<void>;         // 出门探索：POST /api/explore（无 topic）
  onStep: (e: ExplorationStepEvent) => void;
};

export const useExplorationStore = create<ExplorationStoreState>((set) => ({
  wishlist: [],
  liveNodes: [],
  activityId: null,
  addWish: (topic) => {
    const t = topic.trim();
    if (t === "") return;
    set((s) =>
      s.wishlist.includes(t) ? {} : { wishlist: [...s.wishlist, t] },
    );
  },
  removeWish: (topic) =>
    set((s) => ({ wishlist: s.wishlist.filter((w) => w !== topic) })),
  start: async () => {
    const { activity_id } = await postExplore();
    set({ activityId: activity_id, liveNodes: [] });
  },
  onStep: (e) => {
    const node = e.node;
    if (typeof node?.name !== "string") return; // 运行时收窄（01-sse §4.1）
    set((s) =>
      s.activityId === e.activity_id
        ? { liveNodes: [...s.liveNodes, node] }
        : { activityId: e.activity_id, liveNodes: [node] },
    );
  },
}));
```

- [ ] **Step 4: 跑全量前端测试 + tsc**

Run: `cd frontend && npx vitest run tests/sse.test.ts tests/stores.test.ts tests/api.test.ts && npx tsc --noEmit`
Expected: 全绿（Task 5 的 `exploration_step` dispatch 测试此刻补上 `onStep` 实现后通过）。

- [ ] **Step 5: 更新 test-inventory（前端） + commit**

`docs/test-inventory.md` 追加 `explorationStore`（addWish/removeWish/onStep/start）、`postExplore`、`dispatchEvent exploration_step`（功能正确，探索前端，探索升级阶段）。

```bash
git add frontend/src/types/api.ts frontend/src/hooks/useSSE.ts frontend/src/api/dispatch.ts frontend/src/api/client.ts frontend/src/stores/explorationStore.ts frontend/tests/sse.test.ts frontend/tests/stores.test.ts frontend/tests/api.test.ts docs/test-inventory.md
git commit -m "feat(frontend): 探索类型 + explorationStore + postExplore + SSE 分发

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 7: ExplorationMap 组件 + App 入口

**Files:**
- Create: `frontend/src/components/exploration/ExplorationMap.tsx`
- Modify: `frontend/src/App.tsx`（顶栏「探索」按钮 + 浮窗渲染）
- Modify: `frontend/src/index.css`（地图浮窗样式）
- Test: `frontend/tests/explorationMap.test.tsx`（渲染三类节点 + 交互）

**Interfaces:**
- Consumes: `useExplorationStore`（Task 6）、`useActivityStore.results`（历史足迹）、`ExplorationNode`（Task 5）。
- Produces: `<ExplorationMap onClose={() => void} />`（浮窗组件）。无后续任务依赖。

- [ ] **Step 1: 改失败测试（组件渲染）**

`frontend/tests/explorationMap.test.tsx`（新建，参照 `frontend/tests/avatar.test.tsx` 的 render 用法）：

```tsx
import { render, screen, fireEvent } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import ExplorationMap from "../src/components/exploration/ExplorationMap";
import { useActivityStore } from "../src/stores/activityStore";
import { useExplorationStore } from "../src/stores/explorationStore";

beforeEach(() => {
  useActivityStore.setState({
    data: null,
    results: [
      {
        id: "a1", type: "free_exploration", schedule_block_id: "b1",
        status: "completed", started_at: 1, ended_at: 2,
        progress: {
          result: {
            findings: ["深海鱼会发光"],
            nodes: [{ name: "搜索：深海鱼", url: "", kind: "search" }],
          },
        },
      },
    ],
    error: null,
  });
  useExplorationStore.setState({ wishlist: ["发光生物"], liveNodes: [], activityId: null });
});

afterEach(() => {
  useActivityStore.setState({ data: null, results: null, error: null });
  useExplorationStore.setState({ wishlist: [], liveNodes: [], activityId: null });
});

describe("ExplorationMap", () => {
  it("渲染历史节点 + 心愿单", () => {
    render(<ExplorationMap onClose={() => {}} />);
    expect(screen.getByText("搜索：深海鱼")).toBeTruthy();
    expect(screen.getByText("发光生物")).toBeTruthy();
  });

  it("「出门探索」点击调 start（POST /api/explore）", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ activity_id: "e1" }) });
    vi.stubGlobal("fetch", fetchMock);
    render(<ExplorationMap onClose={() => {}} />);
    fireEvent.click(screen.getByText("出门探索"));
    expect(fetchMock).toHaveBeenCalledWith("/api/explore", expect.objectContaining({ method: "POST" }));
    vi.unstubAllGlobals();
  });

  it("「＋」加心愿调用 addWish", () => {
    render(<ExplorationMap onClose={() => {}} />);
    fireEvent.change(screen.getByPlaceholderText("加一个想探索的主题"), {
      target: { value: "深海鱼" },
    });
    fireEvent.click(screen.getByText("＋"));
    expect(useExplorationStore.getState().wishlist).toContain("深海鱼");
  });
});
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd frontend && npx vitest run tests/explorationMap.test.tsx`
Expected: FAIL —— `ExplorationMap` 组件不存在。

- [ ] **Step 3: 实现组件 + App 入口 + CSS**

`frontend/src/components/exploration/ExplorationMap.tsx`：

```tsx
import { useState } from "react";
import { useActivityStore } from "../../stores/activityStore";
import { useExplorationStore } from "../../stores/explorationStore";
import type { ExplorationNode } from "../../types/api";

// 历史足迹：activityStore.results 里 free_exploration 的 result.nodes（含 findings）。
function history(): { node: ExplorationNode; findings: string[] }[] {
  const results = useActivityStore((s) => s.results);
  return (results ?? [])
    .filter((a) => a.type === "free_exploration")
    .flatMap((a) => {
      const result = a.progress?.result as
        | { nodes?: ExplorationNode[]; findings?: string[] }
        | undefined;
      return (result?.nodes ?? []).map((node) => ({
        node,
        findings: result?.findings ?? [],
      }));
    });
}

export default function ExplorationMap({ onClose }: { onClose: () => void }) {
  const liveNodes = useExplorationStore((s) => s.liveNodes);
  const wishlist = useExplorationStore((s) => s.wishlist);
  const addWish = useExplorationStore((s) => s.addWish);
  const removeWish = useExplorationStore((s) => s.removeWish);
  const start = useExplorationStore((s) => s.start);
  const currentType = useActivityStore((s) => s.data?.current?.type);

  const [topic, setTopic] = useState("");
  const [detail, setDetail] = useState<{ name: string; findings: string[] } | null>(null);

  const past = history();
  const exploring = currentType === "free_exploration";
  const live = exploring ? liveNodes : [];

  return (
    <aside className="exploration-map">
      <header className="exploration-map-head">
        <span>🗺️ 探索地图</span>
        <button className="map-close" onClick={onClose}>✕</button>
      </header>

      <div className="exploration-map-body">
        <button className="map-go" onClick={() => void start()}>出门探索</button>

        <div className="map-nodes">
          {past.map((h, i) => (
            <div
              key={i}
              className={`map-node explored ${h.node.kind}`}
              onClick={() => setDetail({ name: h.node.name, findings: h.findings })}
            >
              <span className="node-glyph">✦</span>
              <span className="node-name">{h.node.name}</span>
            </div>
          ))}
          {live.map((n, i) => (
            <div key={`live-${i}`} className={`map-node live ${n.kind}`}>
              <span className="node-glyph">🦊</span>
              <span className="node-name">{n.name}</span>
            </div>
          ))}
          {wishlist.map((w, i) => (
            <div key={`wish-${i}`} className="map-node pending">
              <span className="node-glyph">◌</span>
              <span className="node-name">{w}</span>
              <button className="map-remove" onClick={() => removeWish(w)}>✕</button>
            </div>
          ))}
        </div>

        <div className="map-add">
          <input
            placeholder="加一个想探索的主题"
            value={topic}
            onChange={(e) => setTopic(e.target.value)}
          />
          <button onClick={() => { addWish(topic); setTopic(""); }}>＋</button>
        </div>
      </div>

      {detail !== null && (
        <div className="map-detail" onClick={() => setDetail(null)}>
          <strong>{detail.name}</strong>
          {detail.findings.map((f, i) => (
            <p key={i}>{f}</p>
          ))}
        </div>
      )}
    </aside>
  );
}
```

`frontend/src/App.tsx`：

1. import 加 `ExplorationMap` + `useState`（已有）。
2. 状态加 `const [mapOpen, setMapOpen] = useState(false);`。
3. 顶栏 `.topbar-right` 里 `connection-state` 之前加按钮：

```tsx
        <div className="topbar-right">
          <button className="explore-toggle" onClick={() => setMapOpen((v) => !v)}>
            探索
          </button>
          <span className="connection-state">{CONNECTION_LABEL[status]}</span>
        </div>
```

4. `AnnounceLayer` 之前渲染浮窗：

```tsx
      {mapOpen && <ExplorationMap onClose={() => setMapOpen(false)} />}
      <StatusBar />
      <AnnounceLayer />
```

`frontend/src/index.css` 追加（最小可用样式）：

```css
.exploration-map {
  position: fixed;
  right: 12px;
  bottom: 64px;
  width: 240px;
  max-height: 60vh;
  overflow-y: auto;
  background: rgba(24, 20, 34, 0.92);
  border: 1px solid rgba(255, 255, 255, 0.18);
  border-radius: 12px;
  padding: 12px;
  color: #f3e9ff;
  z-index: 40;
  box-shadow: 0 8px 24px rgba(0, 0, 0, 0.4);
}
.exploration-map-head { display: flex; justify-content: space-between; margin-bottom: 8px; }
.map-close { background: none; border: none; color: #f3e9ff; cursor: pointer; }
.map-go { width: 100%; padding: 6px 0; margin-bottom: 8px; cursor: pointer; }
.map-node { display: flex; align-items: center; gap: 6px; padding: 4px 0; }
.map-node.explored { cursor: pointer; }
.map-node.pending { color: #9b8bb8; opacity: 0.7; }
.node-glyph { width: 1.2em; }
.map-add { display: flex; gap: 4px; margin-top: 8px; }
.map-add input { flex: 1; min-width: 0; }
.map-detail { margin-top: 8px; padding: 8px; background: rgba(0,0,0,0.3); border-radius: 8px; font-size: 12px; cursor: pointer; }
.map-remove { background: none; border: none; color: #f3e9ff; cursor: pointer; margin-left: auto; }
.explore-toggle { background: none; border: 1px solid rgba(255,255,255,0.25); color: inherit; border-radius: 6px; padding: 2px 8px; cursor: pointer; margin-right: 8px; }
```

- [ ] **Step 4: 跑测试 + tsc 确认通过**

Run: `cd frontend && npx vitest run tests/explorationMap.test.tsx && npx tsc --noEmit`
Expected: PASS。

- [ ] **Step 5: 追平文档 + test-inventory + commit**

`docs/design/exploration-map.md` 状态改「已实现」，并核对 §3/§4 与实现一致。`docs/frontend/01-sse.md` §2/§4 补 `exploration_step` 判别联合 + 分发表行；`docs/frontend/02-stores.md` 补 `explorationStore` 签名（若存在该节）。
`docs/test-inventory.md` 追加 `ExplorationMap` 渲染/交互测试（功能正确，探索前端，探索升级阶段）。

```bash
git add frontend/src/components/exploration/ExplorationMap.tsx frontend/src/App.tsx frontend/src/index.css frontend/tests/explorationMap.test.tsx docs/design/exploration-map.md docs/frontend/01-sse.md docs/test-inventory.md
git commit -m "feat(frontend): 探索地图组件 + 主界面入口

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## 收尾质量门（所有任务完成后一次性跑）

```bash
python -m ruff check nyx/ tests/
python -m pyright nyx/ tests/
python -m pytest -q
cd frontend && npx tsc --noEmit && npx vitest run
```

人工抽查：
- `Exploration.run` 返回 `{findings, notes, nodes}`，`nodes` 是 `result.nodes`（不进 `Activity` dataclass，`progress` 仍是 JSON）。
- `EXPLORATION_STEP` ROUTING 为 `[]`（仅广播），前端 `EVENT_TYPES` + `types/api.ts` 判别联合 + `dispatchEvent` 三处同步。
- `POST /api/explore` 复用 `_execute`，未复制执行逻辑；busy 走 409。
- `should_explore` 无 energy 入参、无 `_FREE_EXPLORATION_ENERGY` orphan。
- 未新增 Repository/Service/Manager 层；心愿单仅前端内存。
