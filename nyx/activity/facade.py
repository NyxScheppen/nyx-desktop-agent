import asyncio
import contextlib
import json
import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, cast

from nyx.activity.exploration import Exploration, should_explore
from nyx.activity.material_store import MaterialStore
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
from nyx.tools.registry import ToolRegistry
from nyx.types import Activity, CurrentState, Event, ShortTermDesire

_logger = logging.getLogger(__name__)

_READ_CONTEXT_CHARS = 6000  # 读物喂 LLM 的字符预算（decision，可推翻）


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


def _harvest_task_exception(task: asyncio.Task[None]) -> None:
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
        material_store: MaterialStore,
        bus: EventBus,
        llm: LlmClient,
        evaluator: Evaluator,
        tools: ToolRegistry,
        desire: DesireFacade,
        get_state: Callable[[], Awaitable[CurrentState]],
        config: ActivityConfig,
        exploration_config: ExplorationConfig,
    ) -> None:
        self._store = store
        self._material_store = material_store
        self._bus = bus
        self._llm = llm
        self._evaluator = evaluator
        self._desire = desire
        self._get_state = get_state
        self._config = config
        self._exploration = Exploration(
            llm, evaluator, tools, exploration_config
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
                    "type": activity.type.value,
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

    async def interrupt(self, activity_id: str, by_event: EventType) -> None:
        """抢占即废弃：校验目标 RUNNING → cancel 执行 task 并 await 其彻底结束
        → 重读守卫（窗口内已自行完成/失败则不覆盖）→ 置 ABANDONED 落库
        + 发布 activity_interrupted。

        执行中的 result 尚未写入，故仅落终态（不持久化部分进度）。
        """
        activity = await self._store.get(activity_id)
        if activity is None or activity.status is not ActivityStatus.RUNNING:
            return
        task = self._task
        if task is not None and not task.done():
            task.cancel()
            # 等 _execute 收尾即可，无论取消还是异常终；失败已由 _execute
            # 记日志 + done_callback 收割，终态交给下方重读守卫，不跳过 ABANDONED
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        activity = await self._store.get(activity_id)   # 重读：已终态则不动
        if activity is None or activity.status is not ActivityStatus.RUNNING:
            return
        activity.status = ActivityStatus.ABANDONED
        activity.ended_at = time.time()
        await self._store.update(activity)
        await self._bus.publish(
            internal_event(
                EventType.ACTIVITY_INTERRUPTED,
                {"activity_id": activity_id, "by": by_event.value},
                activity_id,
            )
        )

    # ---- 读 ----

    async def get_current(self) -> Activity | None:
        return await self._store.get_current()

    async def get_schedule(self) -> list[Activity]:
        return await self._store.list_schedule(_day_start(time.time()))

    async def read_material(
        self, path: str, filename: str, total_chars: int, correlation_id: str
    ) -> None:
        """用户投喂资料：注册进书库 → 立即发起一次 READING 活动读第一块。

        与 _maybe_start_activity 共用 _start_lock 串行守卫；忙时跳过（文件已落盘、
        且已入书库，探索欲后续会自行续读，不排队）。结果经 activity_start/activity_end
        SSE 可见。
        """
        await self._material_store.upsert(path, filename, total_chars, time.time())
        async with self._start_lock:
            if self._task is not None and not self._task.done():
                return
            now = time.time()
            activity = Activity(
                id=str(uuid.uuid4()),
                type=ActivityType.READING,
                schedule_block_id=format_time_label(
                    0, self._config.grid_minutes, _elapsed_hours(now)
                ),
                status=ActivityStatus.PENDING,
                progress={
                    "source": path,
                    "filename": filename,
                    "description": filename,
                    "read_chars": 0,
                    "total_chars": total_chars,
                    "correlation_id": correlation_id,
                },
                started_at=now,
            )
            await self._store.insert(activity)
            self._task = asyncio.create_task(self._execute(activity))
            self._task.add_done_callback(_harvest_task_exception)

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
                material = await self._material_store.next_readable()
                if material is not None:
                    # 读最近未读完的那本，从它的进度续读（绝不编造）
                    activity.progress["source"] = material.path
                    activity.progress["filename"] = material.filename
                    activity.progress["description"] = material.filename
                    activity.progress["read_chars"] = material.read_chars
                    activity.progress["total_chars"] = material.total_chars
                else:
                    # 无书可读：转自由探索（沿用限速）；限速中则退回默认活动，
                    # 任何情况都不让 LLM 凭空编造读书内容
                    last = await self._store.get_last_exploration()
                    if should_explore(
                        state.energy,
                        last,
                        self._exploration_config.rate_limit_hours,
                        time.time(),
                    ):
                        activity.type = ActivityType.FREE_EXPLORATION
                    else:
                        activity = self._default_activity(state)
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
            source = activity.progress.get("source")
            if source is None:
                # READING 必须有真实读物；缺 source 说明上游决策出错，fail-fast
                raise ValueError("读书活动缺 source：已禁止凭空编造")
            return await self._run_reading_source(activity, str(source))
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

    async def _run_reading_source(
        self, activity: Activity, source: str
    ) -> dict[str, Any]:
        """分块读真实文件：切 [read_chars, read_chars+6000) 一块喂 LLM 产 {book, note}，
        推进书库进度并带回 read_chars/total_chars（绝不凭空编造）。"""
        content = await asyncio.to_thread(
            Path(source).read_text, encoding="utf-8", errors="replace"
        )
        read_chars = int(activity.progress.get("read_chars", 0))
        chunk = content[read_chars : read_chars + _READ_CONTEXT_CHARS]
        if chunk == "":
            # 已读到末尾（或文件比注册时短）：无新内容，不调用 LLM 编造
            await self._material_store.advance(source, len(content), time.time())
            return {"read_chars": len(content), "total_chars": len(content)}
        result = await self._run_llm_activity(
            activity, "reading", extra_context=chunk
        )
        new_read_chars = read_chars + len(chunk)
        await self._material_store.advance(source, new_read_chars, time.time())
        result["read_chars"] = new_read_chars
        result["total_chars"] = len(content)
        return result

    async def _run_llm_activity(
        self,
        activity: Activity,
        output_type: str,
        extra_context: str | None = None,
    ) -> dict[str, Any]:
        user_msg = f"活动类型：{activity.type.value}"
        if extra_context:
            user_msg += f"\n读物内容：{extra_context}"
        output = await self._llm.complete(
            [
                {"role": "system", "content": _ACTIVITY_SYSTEM},
                {"role": "user", "content": user_msg},
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
