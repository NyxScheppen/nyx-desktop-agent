import asyncio
import contextlib
import logging
import time
from typing import Any

from nyx.activity.store import ActivityStore
from nyx.config import ActivityConfig
from nyx.desire.facade import DesireFacade
from nyx.enums import ActivityStatus, ActivityType, AssignedTaskStatus, EventType
from nyx.events.bus import EventBus
from nyx.events.event import internal_event
from nyx.types import Activity, Event

_RESUMABLE_TYPES = (
    ActivityType.READING,
    ActivityType.CREATION,
    ActivityType.FREE_EXPLORATION,
)

_logger = logging.getLogger(__name__)


def goal_met(goal: dict[str, Any] | None, result: dict[str, Any]) -> bool:
    """判断一次活动是否完成欲望目标中的一个单位。"""
    if goal is None:
        return True
    if result.get("type") == "free_exploration":
        return result.get("outcome") == "won"
    action = goal.get("action")
    if action == "read":
        return bool(result.get("completed"))
    if action == "write":
        return bool(result.get("title") and result.get("content"))
    if action == "observe":
        return bool(result.get("presence"))
    return False


def activity_goal_signal(activity: Activity) -> bool | None:
    """活动结束事件的欲望结算信号，允许 runner 显式声明“不结算”。"""
    if "goal_signal" in activity.progress:
        signal = activity.progress["goal_signal"]
        if isinstance(signal, bool) or signal is None:
            return signal
    goal = activity.progress.get("goal")
    result = activity.progress.get("result", {})
    return goal_met(goal, result)


def correlation_id(activity: Activity) -> str:
    """活动事件优先沿用欲望 correlation_id，否则回退活动 id。"""
    return str(activity.progress.get("correlation_id") or activity.id)


class ActivityLifecycle:
    """管理活动状态转换及其对应的欲望和事件副作用。"""

    def __init__(
        self,
        store: ActivityStore,
        bus: EventBus,
        desire: DesireFacade,
        config: ActivityConfig,
    ) -> None:
        self._store = store
        self._bus = bus
        self._desire = desire
        self._config = config

    async def start(self, activity: Activity, is_new: bool) -> bool:
        """Atomically claim the source, persist RUNNING, and append start event."""
        previous_status = activity.status
        previous_ended_at = activity.ended_at
        activity.status = ActivityStatus.RUNNING
        activity.ended_at = None
        desire_id = activity.progress.get("desire_id")
        task_id = activity.progress.get("task_id")
        event = internal_event(
            EventType.ACTIVITY_START,
            {
                "activity_id": activity.id,
                "type": activity.type.value,
                "schedule_block_id": activity.schedule_block_id,
            },
            correlation_id(activity),
        )
        started = True
        try:
            async with self._store.db.transaction():
                if isinstance(task_id, str):
                    started = await self._store.claim_task_for_activity(
                        task_id, time.time()
                    )
                elif isinstance(desire_id, str):
                    if is_new:
                        started = await self._desire.claim_for_activity_in_transaction(
                            desire_id
                        )
                    else:
                        started = await self._desire.resume_for_activity_in_transaction(
                            desire_id
                        )
                if started:
                    if is_new:
                        await self._store.insert(activity)
                    else:
                        await self._store.update(activity)
                    await self._bus.append_in_transaction(event)
        except BaseException:
            activity.status = previous_status
            activity.ended_at = previous_ended_at
            raise
        if not started:
            activity.status = previous_status
            activity.ended_at = previous_ended_at
            return False
        await self._announce(event)
        return True

    async def complete(self, activity: Activity) -> str | None:
        """进入 COMPLETED，并发布携带目标结果的活动结束事件。"""
        previous_status = activity.status
        previous_ended_at = activity.ended_at
        activity.status = ActivityStatus.COMPLETED
        activity.ended_at = time.time()
        result = activity.progress.get("result", {})
        signal = activity_goal_signal(activity)
        desire_id = activity.progress.get("desire_id")
        task_id = activity.progress.get("task_id")
        event = internal_event(
            EventType.ACTIVITY_END,
            {
                "activity_id": activity.id,
                "type": activity.type.value,
                "desire_id": desire_id,
                "goal_met": signal,
                "energy_delta": getattr(
                    self._config.energy_delta, activity.type.value
                ),
                "result": result,
            },
            correlation_id(activity),
        )
        try:
            async with self._store.db.transaction():
                if signal is None and isinstance(desire_id, str):
                    await self._desire.release_active(desire_id)
                await self._store.update(activity)
                if isinstance(task_id, str):
                    await self._store.set_task_status(
                        task_id, AssignedTaskStatus.COMPLETED, activity.ended_at
                    )
                await self._bus.append_in_transaction(event)
        except BaseException:
            activity.status = previous_status
            activity.ended_at = previous_ended_at
            raise
        await self._announce(event)
        return task_id if isinstance(task_id, str) else None

    async def fail(self, activity: Activity, error: str) -> str | None:
        """进入 INCOMPLETE，并把消费中的欲望改为 SUPPRESSED。"""
        previous_status = activity.status
        previous_ended_at = activity.ended_at
        activity.status = ActivityStatus.INCOMPLETE
        activity.ended_at = time.time()
        desire_id = activity.progress.get("desire_id")
        task_id = activity.progress.get("task_id")
        try:
            async with self._store.db.transaction():
                await self._store.update(activity)
                if isinstance(desire_id, str):
                    await self._desire.mark_suppressed(desire_id)
                if isinstance(task_id, str):
                    await self._store.set_task_status(
                        task_id,
                        AssignedTaskStatus.FAILED,
                        activity.ended_at,
                        error[:500],
                    )
        except BaseException:
            activity.status = previous_status
            activity.ended_at = previous_ended_at
            raise
        return task_id if isinstance(task_id, str) else None

    async def recover_stale_running(self) -> list[Activity]:
        """启动时清理没有内存 task 承接的 PENDING/RUNNING 活动。"""
        recovered: list[Activity] = []
        recovered_at = time.time()
        async with self._store.db.transaction():
            for activity in await self._store.list_unfinished():
                was_pending = activity.status is ActivityStatus.PENDING
                activity.status = (
                    ActivityStatus.ABANDONED
                    if was_pending or activity.type not in _RESUMABLE_TYPES
                    else ActivityStatus.PAUSED
                )
                activity.ended_at = recovered_at
                await self._store.update(activity)
                desire_id = activity.progress.get("desire_id")
                if isinstance(desire_id, str):
                    if was_pending:
                        await self._desire.release_active(desire_id)
                    else:
                        await self._desire.mark_suppressed(desire_id)
                task_id = activity.progress.get("task_id")
                if isinstance(task_id, str):
                    await self._store.set_task_status(
                        task_id, AssignedTaskStatus.PENDING, recovered_at
                    )
                recovered.append(activity)
            for task in await self._store.list_running_tasks():
                linked = await self._store.get_latest_activity_for_task(task.id)
                if linked is not None and linked.status is ActivityStatus.COMPLETED:
                    status = AssignedTaskStatus.COMPLETED
                elif linked is not None and linked.status is ActivityStatus.INCOMPLETE:
                    status = AssignedTaskStatus.FAILED
                else:
                    status = AssignedTaskStatus.PENDING
                await self._store.set_task_status(task.id, status, recovered_at)
        return recovered

    async def interrupt(
        self,
        activity_id: str,
        by_event: EventType,
        task: asyncio.Task[None] | None,
    ) -> str | None:
        """取消运行任务，并按活动类型进入 PAUSED 或 ABANDONED。"""
        activity = await self._store.get(activity_id)
        if activity is None or activity.status is not ActivityStatus.RUNNING:
            return None
        if task is not None and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        activity = await self._store.get(activity_id)
        if activity is None or activity.status is not ActivityStatus.RUNNING:
            return None
        activity.status = (
            ActivityStatus.PAUSED
            if activity.type in _RESUMABLE_TYPES
            else ActivityStatus.ABANDONED
        )
        activity.ended_at = time.time()
        desire_id = activity.progress.get("desire_id")
        task_id = activity.progress.get("task_id")
        event = internal_event(
            EventType.ACTIVITY_INTERRUPTED,
            {"activity_id": activity_id, "by": by_event.value},
            correlation_id(activity),
        )
        async with self._store.db.transaction():
            await self._store.update(activity)
            if isinstance(desire_id, str):
                await self._desire.mark_suppressed(desire_id)
            if isinstance(task_id, str):
                await self._store.set_task_status(
                    task_id, AssignedTaskStatus.PENDING, activity.ended_at
                )
            await self._bus.append_in_transaction(event)
        await self._announce(event)
        return task_id if isinstance(task_id, str) else None

    async def _announce(self, event: Event) -> None:
        try:
            await self._bus.announce_committed(event)
        except Exception:
            _logger.exception(
                "活动事件提交后广播失败 event_id=%s type=%s",
                event.id,
                event.type.value,
            )
