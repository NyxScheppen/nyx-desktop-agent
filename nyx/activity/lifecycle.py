import asyncio
import contextlib
import time
from collections.abc import Awaitable, Callable
from typing import Any, cast

from nyx.activity.store import ActivityStore
from nyx.config import ActivityConfig
from nyx.desire.facade import DesireFacade
from nyx.enums import ActivityStatus, ActivityType, EventType
from nyx.events.bus import EventBus
from nyx.events.event import internal_event
from nyx.types import Activity

_RESUMABLE_TYPES = (
    ActivityType.READING,
    ActivityType.CREATION,
    ActivityType.FREE_EXPLORATION,
)


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

    async def start(self, activity: Activity) -> None:
        """进入 RUNNING，并发布活动开始事件。"""
        activity.status = ActivityStatus.RUNNING
        desire_id = activity.progress.get("desire_id")
        event = internal_event(
            EventType.ACTIVITY_START,
            {
                "activity_id": activity.id,
                "type": activity.type.value,
                "schedule_block_id": activity.schedule_block_id,
            },
            correlation_id(activity),
        )
        append = getattr(self._bus, "append_in_transaction", None)
        announce = getattr(self._bus, "announce_committed", None)
        if not callable(append) or not callable(announce):
            await self._store.update(activity)
            if isinstance(desire_id, str):
                await self._desire.mark_active(desire_id)
            await self._bus.publish(event)
            return
        append_event = cast(Callable[[Any], Awaitable[tuple[str, ...]]], append)
        announce_event = cast(Callable[[Any], Awaitable[None]], announce)
        async with self._store.db.transaction():
            await self._store.update(activity)
            if isinstance(desire_id, str):
                await self._desire.mark_active(desire_id)
            await append_event(event)
        await announce_event(event)

    async def complete(self, activity: Activity) -> None:
        """进入 COMPLETED，并发布携带目标结果的活动结束事件。"""
        activity.status = ActivityStatus.COMPLETED
        activity.ended_at = time.time()
        result = activity.progress.get("result", {})
        signal = activity_goal_signal(activity)
        desire_id = activity.progress.get("desire_id")
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
        append = getattr(self._bus, "append_in_transaction", None)
        announce = getattr(self._bus, "announce_committed", None)
        if not callable(append) or not callable(announce):
            if signal is None and isinstance(desire_id, str):
                await self._desire.release_active(desire_id)
            await self._store.update(activity)
            await self._bus.publish(event)
            return
        append_event = cast(Callable[[Any], Awaitable[tuple[str, ...]]], append)
        announce_event = cast(Callable[[Any], Awaitable[None]], announce)
        async with self._store.db.transaction():
            if signal is None and isinstance(desire_id, str):
                await self._desire.release_active(desire_id)
            await self._store.update(activity)
            await append_event(event)
        await announce_event(event)

    async def fail(self, activity: Activity) -> None:
        """进入 INCOMPLETE，并把消费中的欲望改为 SUPPRESSED。"""
        activity.status = ActivityStatus.INCOMPLETE
        activity.ended_at = time.time()
        await self._store.update(activity)
        desire_id = activity.progress.get("desire_id")
        if isinstance(desire_id, str):
            await self._desire.mark_suppressed(desire_id)

    async def recover_stale_running(self) -> list[Activity]:
        """启动时清理没有内存 task 承接的 RUNNING 活动。"""
        recovered: list[Activity] = []
        for activity in await self._store.list_running():
            activity.status = (
                ActivityStatus.PAUSED
                if activity.type in _RESUMABLE_TYPES
                else ActivityStatus.ABANDONED
            )
            activity.ended_at = time.time()
            await self._store.update(activity)
            desire_id = activity.progress.get("desire_id")
            if isinstance(desire_id, str):
                await self._desire.mark_suppressed(desire_id)
            recovered.append(activity)
        return recovered

    async def interrupt(
        self,
        activity_id: str,
        by_event: EventType,
        task: asyncio.Task[None] | None,
    ) -> None:
        """取消运行任务，并按活动类型进入 PAUSED 或 ABANDONED。"""
        activity = await self._store.get(activity_id)
        if activity is None or activity.status is not ActivityStatus.RUNNING:
            return
        if task is not None and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        activity = await self._store.get(activity_id)
        if activity is None or activity.status is not ActivityStatus.RUNNING:
            return
        activity.status = (
            ActivityStatus.PAUSED
            if activity.type in _RESUMABLE_TYPES
            else ActivityStatus.ABANDONED
        )
        activity.ended_at = time.time()
        desire_id = activity.progress.get("desire_id")
        event = internal_event(
            EventType.ACTIVITY_INTERRUPTED,
            {"activity_id": activity_id, "by": by_event.value},
            correlation_id(activity),
        )
        append = getattr(self._bus, "append_in_transaction", None)
        announce = getattr(self._bus, "announce_committed", None)
        if not callable(append) or not callable(announce):
            await self._store.update(activity)
            if isinstance(desire_id, str):
                await self._desire.mark_suppressed(desire_id)
            await self._bus.publish(event)
            return
        append_event = cast(Callable[[Any], Awaitable[tuple[str, ...]]], append)
        announce_event = cast(Callable[[Any], Awaitable[None]], announce)
        async with self._store.db.transaction():
            await self._store.update(activity)
            if isinstance(desire_id, str):
                await self._desire.mark_suppressed(desire_id)
            await append_event(event)
        await announce_event(event)
