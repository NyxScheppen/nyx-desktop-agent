# ActivityFacade + 行为链 + 观察

> 范围：`activity/store.py`（ActivityStore，新增）+ `activity/facade.py`（ActivityFacade）+ `activity/exploration.py`（跨域行为链 LangGraph）+ `activity/observe.py`（观察用户判定）。
> 活动系统是「欲望的消费端」（design §1.3）：把 `DesireFacade.get_pending()` 的欲望映射成日程块活动、执行、判定 goal、发布 `activity_end` 让 desire/inner_life 消费回写。13-activity-scheduler 的四个纯函数（`desire_to_activity` / `rank_desires` / `build_schedule` / `format_time_label`）是本 spec 的决策底座。
> **本文件自包含**：四个文件的完整代码内联在下文。

## 元信息

- **前置依赖**：05-event（`EventBus` / ROUTING）、06-tools（`ToolRegistry`）、09-memory-facade（`MemoryFacade.search`）、11-desire（`DesireFacade.get_pending`/`get_all`）、12-inner-life（`InnerLifeFacade.get_state` + `activity_end` 的 `energy_delta` 契约）、13-activity-scheduler（四个纯函数）、02-config（`ActivityConfig` / `ExplorationConfig`）、03-llm（`LlmClient.complete`）、04-db（`activity` 表）、15-eval（`Evaluator`）

## 用户故事

> 作为 Nyx 系统的开发者，我想要活动系统的门面——`on_tick`/`on_desire_generated` 触发消费欲望、`select_activity` 选活动、后台 task 执行（读书/创作/发呆/自由探索/观察/休息）、`complete_activity` 判定 goal 并发布 `activity_end`、`interrupt` 软中断存进度、`get_current`/`get_schedule` 供仪表盘——以便欲望「达峰→生成→被消费→满足回写」闭环，前端能看到活动时间线、打断点、进度。

## 验收标准

- [ ] `store.py` 含 `ActivityStore`（`insert` / `get` / `get_current` / `get_last_exploration` / `list_schedule` / `update`），与「`activity/store.py`（完整）」段逐字一致
- [ ] `facade.py` 含 `ActivityFacade`：`on_tick(tick_type) -> None` / `on_desire_generated(event) -> None` / `select_activity(desires, state) -> Activity | None` / `complete_activity(activity) -> None` / `interrupt(activity_id, by) -> None` / `get_current() -> Activity | None` / `get_schedule() -> list[Activity]`
- [ ] `select_activity` 纯决策：无欲望→`None`；精力不足→`REST`；否则第一个可排程欲望→映射活动，`progress` 存 `desire_id`/`goal`/`correlation_id`/`description`
- [ ] `READING` 升级 `FREE_EXPLORATION`：探索欲映射的读书在 `_maybe_start_activity` 里经 `should_explore`（精力充足 + 频率上限）判定升级；频率上限内降级为普通读书
- [ ] 空槽默认：`select_activity` 返回 `None`（无欲望/全互动欲）时 `_maybe_start_activity` 产 `_default_activity`（精力疲惫 `< ENERGY_REST_THRESHOLD`→`IDLE_REFLECTION`、否则→`OBSERVE_USER`），`progress["desire_id"] is None`
- [ ] 活动执行在**后台 task**（不阻塞事件总线）；`activity_start`/`activity_end`/`activity_interrupted` 由 facade 自己 `publish`、`source=INTERNAL`
- [ ] `complete_activity`：goal 判定（`_goal_met` 纯函数）→ `status=COMPLETED` → 发布 `activity_end`（content 含 `activity_id`/`desire_id`/`goal_met`/`energy_delta`/`result`）
- [ ] `interrupt`：先校验目标 activity 存在且 RUNNING → cancel 执行 task → `status=PAUSED` + 发布 `activity_interrupted`（content `{activity_id, by}`）
- [ ] `exploration.py` 含 `Exploration`（LangGraph 图）+ `should_explore` 纯函数；`web_enabled=false` 时不注册 `search_web` 节点；节点内 LLM 调用带 `correlation_id` 溯源
- [ ] `observe.py` 含 `classify_presence` 纯函数（活跃度+窗口标题 → `"online"`/`"away"`/`"busy"`）
- [ ] 两处 LLM 产出后紧跟 `await evaluator.evaluate(output)`：`_run_llm_activity`（`output_type` "reading"/"creation"）与 `Exploration._plan_next`（`output_type="exploration_plan"`）
- [ ] `pyright` strict 零报错

## 技术方案

- **新文件**：`nyx/activity/store.py`、`nyx/activity/facade.py`、`nyx/activity/exploration.py`、`nyx/activity/observe.py`（`scheduler.py` 归 13）
- **库**：`langgraph`（仅 `StateGraph` / `START` / `END` / 条件边；版本敏感契约以锁定版本为准，同 03-llm 依赖 pin 约定）
- **ActivityStore 归属**：memory/desire/inner_life 各有 `store.py`，activity 保持一致——facade 不直接写 SQL（三层：Facade → 子系统 → 内部类）。tech-ref §7 ripple：`activity/` 补一行 `store.py  # ActivityStore（activity 表单表 CRUD）`
- **依赖解环（遵守 12 §54）**：`inner_life → {activity, desire}` 已锁，故 `ActivityFacade` **不持有 `InnerLifeFacade`**，注入 `get_state: Callable[[], Awaitable[CurrentState]]` 回调（组合根用 `inner_life.get_state` 绑定）。`select_activity(desires, state)` 以参数收 `CurrentState`（纯决策，无环）；`DesireFacade` 依赖单向（activity → desire，读队列/values），不成环
- **两个事件入口都归到 `_maybe_start_activity`**：`SCHEDULE_BLOCK_START` tick（每小时一块）与 `DESIRE_GENERATED`（欲望刚生成）都「有空闲就消费」。区别是触发时机，逻辑共用；有 running 活动则忽略（等它完成或下一个触发）
- **`select_activity` 返回 `Activity | None`**：无欲望 / 全互动欲时无活动可排，返回 `None`（空槽）。tech-ref §5 原签名 `-> Activity` 需 ripple 为 `-> Activity | None`（见完成定义）
- **活动执行 = 后台 task**：05-event「顺序分发、逐个 await handler」，若 on_tick 里 await 完整个活动（LLM 秒级、探索链分钟级）会阻塞事件总线、吞掉用户消息打断。故 `_maybe_start_activity` 用 `asyncio.create_task` 启动执行后立即返回；`interrupt` 靠 `self._task.cancel()` 软打断
- **并发守卫（同一时刻仅一个活动）**：`_start_lock` 串行化「查 running → insert PENDING → 翻 RUNNING」决策；但 `_execute` 在锁外异步翻 RUNNING，仅靠 `get_current`（只匹配 running/paused，见 store）会留 TOCTOU 窗口（PENDING 已 insert 却查不到 running）。故锁内先同步查 `self._task` 未完成即 `return` 闭合窗口；`self._task` 在锁内赋值，天然串行
- **执行失败 = INCOMPLETE + 上抛**：`_execute` 失败落 `INCOMPLETE`（`ended_at` 已记）后仍 `raise`（不吞异常）；`logger.exception` 记录详情，`add_done_callback(_harvest_task_exception)` 收割 fire-and-forget task 的异常，避免 asyncio「Task exception was never retrieved」警告静默漂着
- **自由探索升级（design §8.6，13 已委托给 14）**：`select_activity` 保持基线映射（探索欲→`READING`），升级判定放 `_maybe_start_activity`（那里有 store/config/now，`select_activity` 保持纯决策）。「探索欲」条件由结构保证——`READING` 活动**仅**由 `DesireType.EXPLORATION` 映射而来（13 `desire_to_activity`），故调用方在 `activity.type is READING` 时才调 `should_explore`（只查精力 + 频率两项）
- **六种活动执行分派（`_run_activity`）**：
  - `READING` / `CREATION`：1 次 LLM（`json_mode=True`、`module="activity"`、`output_type="reading"`/`"creation"`）→ result `{book, note}` / `{title, content}`
  - `IDLE_REFLECTION`：**发布 `REFLECTION` 事件**（12 §51 消费后内部 `reflect()`，1 LLM 在 inner_life），activity 不直接调 `InnerLifeFacade.reflect`；result `{}`
  - `FREE_EXPLORATION`：调 `Exploration.run()`（LangGraph 多步，seed = 欲望描述）→ result `{findings, notes}`
  - `OBSERVE_USER` / `REST`：0 LLM，result `{}`
- **空槽默认（design §8.2 观察/发呆，13 §30 委托 14）**：`select_activity` 返回 `None`（无欲望/全互动欲）时 `_maybe_start_activity` 产 `_default_activity`——精力疲惫（`< ENERGY_REST_THRESHOLD`，从 12 `inner_life.emotion` 共享导入）→ `IDLE_REFLECTION`（+10 微恢复 + 发布 `REFLECTION` 触发 reflect），否则 → `OBSERVE_USER`（-10 消耗 + 情报收集）。这是 `IDLE_REFLECTION`/`OBSERVE_USER` 的唯一触发来源（非欲望驱动、不进 13 `build_schedule`），补上后两条分支可达，不再死代码
- **`activity_end` content 契约（11 §49 + 12 §45 引用，本 spec 定义完整形状）**：`{"activity_id": str, "desire_id": str | None, "goal_met": bool | None, "energy_delta": float, "result": dict}`。`desire_id`/`goal_met` 由 11 `satisfy_from_activity_end` 消费（缺键/错类型跳过）；`energy_delta` 由 12 `_apply_energy` 消费（缺省 0）；`result` 进 SSE payload（tech-ref §4）
- **`energy_delta` 取值**：`getattr(config.energy_delta, activity.type.value)`（`ActivityType.value` 与 `ActivityEnergyDelta` 字段名 1:1，`reading→-20`、`creation→-25`、`free_exploration→-30`、`observe_user→-10`、`idle_reflection→+10`、`rest→+30`），不用 if-elif（六键自然对应）
- **goal 判定（MVP，可推翻）**：`_goal_met(goal, result)` = goal 非 None 且 `result` 非空 → `True`；goal None（观察/休息）→ `None`。
- **精力门槛**：`select_activity` 用 13 的 `build_schedule(desires, state.energy, energy_delta)` 取 `[0]`（精力跌破阈值自动穿插 `REST`），不另写门槛逻辑；`schedule[0] is REST` → 无关联 desire
- **`get_schedule()` 语义**：返回「今日已产生的 Activity 记录」（`started_at >= 今日零点`，`list_schedule`），按 `started_at ASC`。未来计划不持久化（design §8.1），前端按 grid 渲染空槽；`_day_start` 纯函数算当日零点（MVP 用 UTC 日边界，可推翻）
- **`interrupt` 的 `by: EventType`**：打断原因（`USER_MESSAGE` / `INITIATE_CHAT`）。谁调 `interrupt` 归 17/18（用户消息/搭话打断活动）；14 只提供方法 + 发布 `activity_interrupted`。MVP 简化为 `interrupt` 存进度留 `PAUSED`
- **`observe.py` 与观察状态的分工**：`classify_presence` 是「在线/离开/忙碌」三态判定的**单一事实来源**（纯函数、单测锁定）。采集（键盘/鼠标活跃度 + 前台窗口标题）在前端 Tauri 壳（design §2 进程边界），判定结果作为 `OBSERVATION_STATE` 事件推给 Python，ROUTING 到 inner_life + desire。**`classify_presence` 的运行时调用方是前端 ingress，不在本 spec 的 backend 范围内**（前端 spec 推迟）——保留它是为了让「判定规则」在 Python 侧可展示（原则 3）+ 可溯源（原则 5）。`OBSERVE_USER` 活动本身是 0-LLM 占位（result `{}`）
- **明确不做**：不建「计划」表（design §8.1 临时概念）；`classify_presence` 只覆盖键盘/鼠标/窗口三输入

### `activity/store.py`（完整）

```python
import json

import aiosqlite

from nyx.db import Database
from nyx.enums import ActivityStatus, ActivityType
from nyx.types import Activity

_COLS = "id, type, schedule_block_id, status, progress, started_at, ended_at"


class ActivityStore:
    """activity 表单表 CRUD。

    所有读写都 `async with self._db.lock:` 串行化（同 05/07/11）。
    """

    def __init__(self, db: Database) -> None:
        self._db = db

    async def insert(self, activity: Activity) -> None:
        async with self._db.lock:
            await self._db.conn.execute(
                "INSERT INTO activity (id, type, schedule_block_id, status, progress, "
                "started_at, ended_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    activity.id,
                    activity.type.value,
                    activity.schedule_block_id,
                    activity.status.value,
                    json.dumps(activity.progress),
                    activity.started_at,
                    activity.ended_at,
                ),
            )
            await self._db.conn.commit()

    async def get(self, activity_id: str) -> Activity | None:
        async with self._db.lock:
            cursor = await self._db.conn.execute(
                f"SELECT {_COLS} FROM activity WHERE id = ?", (activity_id,),
            )
            row = await cursor.fetchone()
        return _row_to_activity(row) if row is not None else None

    async def get_current(self) -> Activity | None:
        """当前活动（running/paused），取最新一条。"""
        async with self._db.lock:
            cursor = await self._db.conn.execute(
                f"SELECT {_COLS} FROM activity WHERE status IN ('running', 'paused') "
                "ORDER BY started_at DESC LIMIT 1",
            )
            row = await cursor.fetchone()
        return _row_to_activity(row) if row is not None else None

    async def get_last_exploration(self) -> float:
        """最近一次自由探索活动的 started_at；从未探索返回 0.0（供频率上限判定）。"""
        async with self._db.lock:
            cursor = await self._db.conn.execute(
                "SELECT MAX(started_at) AS t FROM activity "
                "WHERE type = 'free_exploration'"
            )
            row = await cursor.fetchone()
        return row["t"] if row is not None and row["t"] is not None else 0.0

    async def list_schedule(self, start: float) -> list[Activity]:
        """今日已产生记录（started_at >= start），按 started_at ASC。"""
        async with self._db.lock:
            cursor = await self._db.conn.execute(
                f"SELECT {_COLS} FROM activity WHERE started_at >= ? "
                "ORDER BY started_at ASC",
                (start,),
            )
            rows = await cursor.fetchall()
        return [_row_to_activity(r) for r in rows]

    async def update(self, activity: Activity) -> None:
        async with self._db.lock:
            await self._db.conn.execute(
                "UPDATE activity SET type = ?, schedule_block_id = ?, status = ?, "
                "progress = ?, started_at = ?, ended_at = ? WHERE id = ?",
                (
                    activity.type.value,
                    activity.schedule_block_id,
                    activity.status.value,
                    json.dumps(activity.progress),
                    activity.started_at,
                    activity.ended_at,
                    activity.id,
                ),
            )
            await self._db.conn.commit()


def _row_to_activity(row: aiosqlite.Row) -> Activity:
    return Activity(
        id=row["id"],
        type=ActivityType(row["type"]),
        schedule_block_id=row["schedule_block_id"],
        status=ActivityStatus(row["status"]),
        progress=json.loads(row["progress"]),
        started_at=row["started_at"],
        ended_at=row["ended_at"],
    )
```

### `activity/facade.py`（完整）

```python
import asyncio
import json
import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from typing import Any, cast

from nyx.activity.exploration import Exploration, should_explore
from nyx.activity.scheduler import (
    build_schedule,
    desire_to_activity,
    format_time_label,
    rank_desires,
)
from nyx.activity.store import ActivityStore
from nyx.config import ActivityConfig, ExplorationConfig
from nyx.desire.facade import DesireFacade
from nyx.enums import ActivityStatus, ActivityType, DesireType, EventType, TickType
from nyx.eval.evaluator import Evaluator
from nyx.events.bus import EventBus
from nyx.events.event import SECONDS_PER_DAY, SECONDS_PER_HOUR, internal_event
from nyx.inner_life.emotion import ENERGY_REST_THRESHOLD
from nyx.llm.client import LlmClient
from nyx.memory.facade import MemoryFacade
from nyx.tools.registry import ToolRegistry
from nyx.types import Activity, CurrentState, Event, ShortTermDesire

_logger = logging.getLogger(__name__)


def _day_start(now: float) -> float:
    """当日零点（UTC 日边界，MVP 可推翻为本地时区）。纯函数。"""
    return now - now % SECONDS_PER_DAY


def _elapsed_hours(now: float) -> float:
    """当日已过小时数（浮点）。纯函数。"""
    return (now % SECONDS_PER_DAY) / SECONDS_PER_HOUR


def _goal_met(goal: dict[str, Any] | None, result: dict[str, Any]) -> bool | None:
    """Goal 完成判定（纯函数）。

    MVP：goal 非 None 且 result 非空 → True；goal None → None。
    """
    if goal is None:
        return None
    return bool(result)


def _empty_progress() -> dict[str, Any]:
    """活动 progress 初始模板（desire_id/goal/correlation_id 三键为空）。"""
    return {"desire_id": None, "goal": None, "correlation_id": None}


def _correlation_id(activity: Activity) -> str:
    """活动事件/LLM 溯源 id：优先欲望 correlation_id，退活动自身 id。"""
    return str(activity.progress.get("correlation_id") or activity.id)


def _harvest_task_exception(task: asyncio.Future[None]) -> None:
    """收割后台 task 异常，避免 asyncio 'Task exception was never retrieved' 警告。

    真正的失败详情已在 _execute 的 except 块里经 logger.exception 记录；
    这里只负责把异常标记为「已检索」，不重复记录。
    """
    if not task.cancelled():
        task.exception()


def _parse_activity_result(raw: str, output_type: str) -> dict[str, Any]:
    """LLM 活动结果解析 + 结构校验（对齐 11/12 的 _parse_* fail-fast 风格）。

    reading 需 {book, note}、creation 需 {title, content}；
    缺键 raise（非法结构不静默吞）。
    """
    data: Any = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError(f"活动结果 JSON 应是对象，得到 {type(data).__name__}")
    parsed = cast(dict[str, Any], data)
    required = ("book", "note") if output_type == "reading" else ("title", "content")
    if not all(k in parsed for k in required):
        raise ValueError(f"活动结果 JSON 缺键：{required}")
    return parsed


class ActivityFacade:
    """活动模块门面：消费欲望 → 选活动 → 后台执行 → 完成/打断 → 发布事件。

    依赖注入解环：不持有 InnerLifeFacade，注入 get_state 回调
    （组合根绑 inner_life.get_state）。
    """

    def __init__(
        self,
        store: ActivityStore,
        bus: EventBus,
        llm: LlmClient,
        evaluator: Evaluator,
        tools: ToolRegistry,
        memory: MemoryFacade,
        desire: DesireFacade,
        get_state: Callable[[], Awaitable[CurrentState]],
        config: ActivityConfig,
        exploration_config: ExplorationConfig,
    ) -> None:
        self._store = store
        self._bus = bus
        self._llm = llm
        self._evaluator = evaluator
        self._desire = desire
        self._get_state = get_state
        self._config = config
        self._exploration = Exploration(
            llm, evaluator, tools, memory, exploration_config
        )
        self._exploration_config = exploration_config
        self._start_lock = asyncio.Lock()
        self._task: asyncio.Task[None] | None = None

    # ---- 事件入口 ----

    async def on_tick(self, tick_type: TickType) -> None:
        """SCHEDULE_BLOCK_START：日程块开始，有空闲就消费欲望。"""
        if tick_type is TickType.SCHEDULE_BLOCK_START:
            await self._maybe_start_activity()

    async def on_desire_generated(self, event: Event) -> None:
        """DESIRE_GENERATED：欲望刚生成，有空闲就立即消费。"""
        await self._maybe_start_activity()

    # ---- 决策 ----

    def select_activity(
        self, desires: list[ShortTermDesire], state: CurrentState
    ) -> Activity | None:
        """选下一个活动（纯决策，无 I/O）。

        desires 已由 _maybe_start_activity 排序（rank_desires）。
        """
        if not desires:
            return None
        target = next(
            (d for d in desires if desire_to_activity(d.type) is not None), None
        )
        if target is None:
            return None
        schedule = build_schedule(desires, state.energy, self._config.energy_delta)
        if not schedule:
            return None
        activity_type = schedule[0]
        if activity_type is ActivityType.REST and target.type is not DesireType.REST:
            # 精力恢复穿插的 REST（队首非休息欲却产出 REST = 精力不足），
            # 无关联 desire
            target = None
        now = time.time()
        progress = _empty_progress()
        if target is not None:
            progress["desire_id"] = target.id
            progress["correlation_id"] = target.id
            progress["description"] = target.description
            if target.goal is not None:
                progress["goal"] = {
                    "action": target.goal.action.value,
                    "count": target.goal.count,
                    "topic": target.goal.topic,
                }
        return Activity(
            id=str(uuid.uuid4()),
            type=activity_type,
            schedule_block_id=format_time_label(
                0, self._config.grid_minutes, _elapsed_hours(now)
            ),
            status=ActivityStatus.PENDING,
            progress=progress,
            started_at=now,
        )

    def _default_activity(self, state: CurrentState) -> Activity:
        """空槽默认（design §8.2 观察/发呆，13 §30 委托 14）：无欲望可消费时，
        精力疲惫 → 发呆反思（+10 恢复 + 反思），否则 → 观察用户（-10 情报收集）。
        纯决策，无 I/O。
        """
        activity_type = (
            ActivityType.IDLE_REFLECTION
            if state.energy < ENERGY_REST_THRESHOLD
            else ActivityType.OBSERVE_USER
        )
        now = time.time()
        return Activity(
            id=str(uuid.uuid4()),
            type=activity_type,
            schedule_block_id=format_time_label(
                0, self._config.grid_minutes, _elapsed_hours(now)
            ),
            status=ActivityStatus.PENDING,
            progress=_empty_progress(),
            started_at=now,
        )

    # ---- 生命周期 ----

    async def complete_activity(self, activity: Activity) -> None:
        """完成：goal 判定 + 收尾 + 发布 activity_end（desire/inner_life 消费）。"""
        activity.status = ActivityStatus.COMPLETED
        activity.ended_at = time.time()
        await self._store.update(activity)
        goal = activity.progress.get("goal")
        result = activity.progress.get("result", {})
        await self._bus.publish(
            internal_event(
                EventType.ACTIVITY_END,
                {
                    "activity_id": activity.id,
                    "desire_id": activity.progress.get("desire_id"),
                    "goal_met": _goal_met(goal, result),
                    "energy_delta": getattr(
                        self._config.energy_delta, activity.type.value
                    ),
                    "result": result,
                },
                _correlation_id(activity),
            )
        )

    async def interrupt(self, activity_id: str, by: EventType) -> None:
        """软中断：校验目标 RUNNING，cancel 执行 task、置 PAUSED 落库
        + 发布 activity_interrupted。

        执行中的 result 尚未写入，故仅落 PAUSED 状态（不持久化部分进度）。
        """
        activity = await self._store.get(activity_id)
        if activity is None or activity.status is not ActivityStatus.RUNNING:
            return
        if self._task is not None and not self._task.done():
            self._task.cancel()
        activity.status = ActivityStatus.PAUSED
        await self._store.update(activity)
        await self._bus.publish(
            internal_event(
                EventType.ACTIVITY_INTERRUPTED,
                {"activity_id": activity_id, "by": by.value},
                activity_id,
            )
        )

    # ---- 读 ----

    async def get_current(self) -> Activity | None:
        return await self._store.get_current()

    async def get_schedule(self) -> list[Activity]:
        return await self._store.list_schedule(_day_start(time.time()))

    # ---- 内部 ----

    async def _maybe_start_activity(self) -> None:
        async with self._start_lock:
            if self._task is not None and not self._task.done():
                return
            current = await self._store.get_current()
            if current is not None and current.status is ActivityStatus.RUNNING:
                return
            desires = await self._desire.get_pending()
            values = (await self._desire.get_all()).values
            ranked = rank_desires(desires, values)
            state = await self._get_state()
            activity = self.select_activity(ranked, state)
            if activity is None:
                activity = self._default_activity(state)
            if activity.type is ActivityType.READING:
                last = await self._store.get_last_exploration()
                if should_explore(
                    state.energy,
                    last,
                    self._exploration_config.rate_limit_hours,
                    time.time(),
                ):
                    activity.type = ActivityType.FREE_EXPLORATION
            await self._store.insert(activity)
            self._task = asyncio.create_task(self._execute(activity))
            self._task.add_done_callback(_harvest_task_exception)

    async def _execute(self, activity: Activity) -> None:
        activity.status = ActivityStatus.RUNNING
        await self._store.update(activity)
        await self._bus.publish(
            internal_event(
                EventType.ACTIVITY_START,
                {
                    "activity_id": activity.id,
                    "type": activity.type.value,
                    "schedule_block_id": activity.schedule_block_id,
                },
                _correlation_id(activity),
            )
        )
        try:
            result = await self._run_activity(activity)
        except Exception:
            # fail-fast：失败态落库后仍上抛（不吞异常），但活动不卡 RUNNING
            activity.status = ActivityStatus.INCOMPLETE
            activity.ended_at = time.time()
            await self._store.update(activity)
            _logger.exception(
                "活动执行失败 activity_id=%s type=%s",
                activity.id,
                activity.type.value,
            )
            raise
        activity.progress["result"] = result
        await self.complete_activity(activity)

    async def _run_activity(self, activity: Activity) -> dict[str, Any]:
        t = activity.type
        if t is ActivityType.READING:
            return await self._run_llm_activity(activity, "reading")
        if t is ActivityType.CREATION:
            return await self._run_llm_activity(activity, "creation")
        if t is ActivityType.IDLE_REFLECTION:
            await self._bus.publish(
                internal_event(
                    EventType.REFLECTION,
                    {"activity_id": activity.id},
                    _correlation_id(activity),
                )
            )
            return {}
        if t is ActivityType.FREE_EXPLORATION:
            return await self._exploration.run(
                seed=str(activity.progress.get("description") or activity.id),
                correlation_id=_correlation_id(activity),
            )
        if t in (ActivityType.OBSERVE_USER, ActivityType.REST):
            return {}
        raise ValueError(f"未知活动类型 {t!r}")

    async def _run_llm_activity(
        self, activity: Activity, output_type: str
    ) -> dict[str, Any]:
        output = await self._llm.complete(
            [
                {"role": "system", "content": _ACTIVITY_SYSTEM},
                {"role": "user", "content": f"活动类型：{activity.type.value}"},
            ],
            module="activity",
            output_type=output_type,
            correlation_id=_correlation_id(activity),
            json_mode=True,
        )
        await self._evaluator.evaluate(output)
        return _parse_activity_result(output.content, output_type)


_ACTIVITY_SYSTEM = (
    "你是尼克斯。按 JSON 输出活动结果，键随活动类型："
    "读书 {book, note}、创作 {title, content}。"
)
```

### `activity/exploration.py`（完整）

```python
# pyright: reportMissingTypeStubs=false, reportUnknownMemberType=false
# langgraph 类型标注松散：add_node/compile/ainvoke 返回部分未知、graph.state 缺 stub
import json
from collections.abc import Hashable
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from nyx.config import ExplorationConfig
from nyx.eval.evaluator import Evaluator
from nyx.events.event import SECONDS_PER_HOUR
from nyx.llm.client import LlmClient
from nyx.memory.facade import MemoryFacade
from nyx.tools.registry import ToolRegistry
from nyx.types import Memory

_MAX_STEPS = 8                    # 探索链最大步数（可推翻）
_FREE_EXPLORATION_ENERGY = 60.0   # 探索需精力 >= 此值（可推翻，design §8.6）


class ExplorationState(TypedDict):
    seed: str
    focus: str
    findings: list[str]
    notes: list[str]
    related: list[Memory]
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
    """跨域行为链（LangGraph）：好奇 → 搜索 → 读 → 写笔记 → 联想记忆（design §8.6）。"""

    def __init__(
        self,
        llm: LlmClient,
        evaluator: Evaluator,
        tools: ToolRegistry,
        memory: MemoryFacade,
        exploration_config: ExplorationConfig,
    ) -> None:
        self._llm = llm
        self._evaluator = evaluator
        self._tools = tools
        self._memory = memory
        self._web_enabled = exploration_config.web_enabled
        self._actions = ["search_local", "read", "write_note", "recall_memory"]
        if self._web_enabled:
            self._actions.append("search_web")
        self._graph = self._build_graph()

    def _build_graph(self) -> CompiledStateGraph[ExplorationState]:
        g = StateGraph(ExplorationState)
        g.add_node("plan_next", self._plan_next)
        g.add_node("search_local", self._search_local)
        g.add_node("read", self._read)
        g.add_node("write_note", self._write_note)
        g.add_node("recall_memory", self._recall_memory)
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
            "seed": seed, "focus": seed, "findings": [], "notes": [], "related": [],
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

    async def _recall_memory(self, state: ExplorationState) -> ExplorationState:
        memories = await self._memory.search(state["focus"])
        state["related"].extend(memories)
        return state

    async def _finalize(self, state: ExplorationState) -> ExplorationState:
        return state

    def _route(self, state: ExplorationState) -> str:
        if state["done"]:
            return "finalize"
        # MVP：确定性轮转（与 self._actions 对齐，含 search_web 时 5 步一轮），
        # 不靠 LLM 选具体动作
        # step 在 _plan_next 里先 +1，故 -1 对齐到 actions[0]=search_local 起始
        return self._actions[(state["step"] - 1) % len(self._actions)]


_EXPLORATION_PLAN_SYSTEM = (
    "你是尼克斯的探索规划器。按 JSON 输出 {focus, done}，"
    "决定下一步聚焦对象与是否结束。"
)
```

### `activity/observe.py`（完整）

```python
def classify_presence(
    keyboard_active: bool, mouse_active: bool, window_title: str
) -> str:
    """观察用户：键盘/鼠标活跃度 + 前台窗口标题 → 在线/离开/忙碌
    （纯函数，design §8.5）。

    MVP 简化规则：键盘或鼠标活跃 → "online"；否则窗口标题非空 → "busy"；
    否则 "away"。运行时调用方是前端 ingress（Tauri 壳采集后判定的单一事实来源）；
    本 spec 保留为可展示/可测的规则定义。
    """
    if keyboard_active or mouse_active:
        return "online"
    if window_title:
        return "busy"
    return "away"
```

## 测试要点

- [ ] 单元测试 `tests/test_activity/`（`pytest-asyncio`；`db = await connect(":memory:")`；fake `LlmClient.complete` 按 `output_type` 返回 fixture JSON；`EventBus` 真实例 + recording handler，`run()` 作 task；`get_state` 用 fake 回调返回预设 `CurrentState`——同 05/09/11/12 模式）：
  - [ ] **store**（`test_activity_store.py`）：`insert + get` 往返（`progress` JSON 往返、枚举 `.value` 往返）；`get_current` 只取 running/paused 最新一条；`get_last_exploration`（无 free_exploration 记录 → `0.0`，有 → `MAX(started_at)`）；`list_schedule(start)` 按 `started_at >= start` 过滤 + ASC；`update` 改 `status`/`progress`/`ended_at` → `get` 验证
  - [ ] **纯函数**（`test_activity_facade.py`）：`_day_start`（`now=86400*1.5 → 86400.0`）；`_elapsed_hours`（`now=5400 → 1.5`）；`_goal_met`（goal None → None；goal 非 None + result 空 → False；goal 非 None + result 非空 → True）
  - [ ] **select_activity**（fake `get_state` 返回 `energy=80`）：无欲望 → `None`；`[探索欲]` → `type is READING`、`progress["desire_id"] == desire.id`、`goal` 序列化正确、`progress["description"] == desire.description`；`[互动欲]` → `None`（不占日程块）；`[休息欲]` → `type is REST`、`progress["desire_id"] == rest_desire.id`（欲望驱动的 REST 保留关联）；`energy=30` + 探索欲 → `type is REST`、`progress["desire_id"] is None`（精力恢复无关联）
  - [ ] **should_explore**（`test_exploration.py`）：`energy=59` → False；`energy=60` + `now-last < rate_limit_hours*3600` → False；`energy=60` + 频率过 + `last=0.0` → True
  - [ ] **facade 生命周期**：
    - [ ] `_maybe_start_activity`：有 running 活动 → 不新起；无欲望 → 产 `_default_activity`（见空槽默认 bullet）并 insert；有欲望 → insert + 发布 `activity_start` + `activity_end`（`content["desire_id"]`/`goal_met`/`energy_delta`/`result` 正确、`source is INTERNAL`）；READING/CREATION 时 `evaluator.evaluate` 被调 1 次（收到该 `LLMOutput`）
    - [ ] 升级路径：探索欲 + 精力足 + 频率过 → `activity.type is FREE_EXPLORATION`；频率未过 → 降级 `READING`
    - [ ] 空槽默认：无欲望 + `energy=30` → `type is IDLE_REFLECTION`、`progress["desire_id"] is None`；无欲望 + `energy=80` → `type is OBSERVE_USER`、`progress["desire_id"] is None`
    - [ ] `complete_activity`：`status is COMPLETED`、`ended_at` 非 None、发布 `activity_end`（`energy_delta == config.energy_delta.reading` 等）
    - [ ] `interrupt`：RUNNING 活动 → cancel + `status is PAUSED` + 发布 `activity_interrupted`（`content["by"]` 正确）；`activity_id` 不存在 → 不 cancel、不发布
    - [ ] `get_current` / `get_schedule` 委托 store
  - [ ] **exploration**（`test_exploration.py`）：`Exploration` 用 fake llm/fake_evaluator/tools/memory，`web_enabled=false` 时图不含 `search_web`、`run` 返回 `{findings, notes}` 且步数 ≤ `_MAX_STEPS`；`_plan_next` 的 `llm.complete` 收到 `correlation_id == 初始 correlation_id`，且每次 `complete` 后 `evaluator.evaluate` 被调（`output_type="exploration_plan"`）
  - [ ] **observe**（`test_observe.py`）：`classify_presence` 三态判定（活跃→online、窗口标题→busy、无→away）
- [ ] 集成测试：无（LLM 全 mock、DB 用 `:memory:`；与 desire/inner_life 的真实编排归 18-api）
- [ ] E2E 测试：无

## 完成定义

- [ ] `ruff check` 零报错
- [ ] `pyright` 零报错
- [ ] `pytest` 全绿
- [ ] `test-inventory.md` 已更新
- [ ] ripple 同步：tech-ref §7 `activity/` 补 `store.py`；§6.2 `ExplorationState` 补 `correlation_id` 字段；§5 `select_activity` 返回类型 `Activity` → `Activity | None` 且 `async def` → `def`（纯决策，与 `_default_activity` 一致）；`activity_end` content 契约（`desire_id`/`goal_met`/`energy_delta`）与 11 §49 + 12 §45 一致
- [ ] 下游约定：17-expression 搭话/回复打断活动时调 `interrupt(activity_id, by)`；18-api 组合根注入 `get_state=inner_life.get_state`、`evaluator`（给 `ActivityFacade` 与 `Exploration` 的 LLM 产出评分）、订阅 `SCHEDULE_BLOCK_START`（on_tick）与 `DESIRE_GENERATED`（on_desire_generated）
