import asyncio
import contextlib
import time
from typing import Any

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
        await self._store.update(activity)
        desire_id = activity.progress.get("desire_id")
        if isinstance(desire_id, str):
            await self._desire.mark_active(desire_id)
        await self._bus.publish(
            internal_event(
                EventType.ACTIVITY_START,
                {
                    "activity_id": activity.id,
                    "type": activity.type.value,
                    "schedule_block_id": activity.schedule_block_id,
                },
                correlation_id(activity),
            )
        )

    async def complete(self, activity: Activity) -> None:
        """进入 COMPLETED，并发布携带目标结果的活动结束事件。"""
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
                    "goal_met": goal_met(goal, result),
                    "energy_delta": getattr(
                        self._config.energy_delta, activity.type.value
                    ),
                    "result": result,
                },
                correlation_id(activity),
            )
        )

    async def fail(self, activity: Activity) -> None:
        """进入 INCOMPLETE，并把消费中的欲望改为 SUPPRESSED。"""
        activity.status = ActivityStatus.INCOMPLETE
        activity.ended_at = time.time()
        await self._store.update(activity)
        desire_id = activity.progress.get("desire_id")
        if isinstance(desire_id, str):
            await self._desire.mark_suppressed(desire_id)

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
        await self._store.update(activity)
        desire_id = activity.progress.get("desire_id")
        if isinstance(desire_id, str):
            await self._desire.mark_suppressed(desire_id)
        await self._bus.publish(
            internal_event(
                EventType.ACTIVITY_INTERRUPTED,
                {"activity_id": activity_id, "by": by_event.value},
                activity_id,
            )
        )
