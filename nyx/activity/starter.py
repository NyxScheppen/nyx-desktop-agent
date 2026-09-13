import asyncio
import uuid
from collections.abc import Awaitable, Callable, Coroutine
from typing import Any, cast

from nyx.activity.exploration import should_explore
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
from nyx.enums import ActivityStatus, ActivityType, DesireType
from nyx.inner_life.emotion import ENERGY_REST_THRESHOLD
from nyx.types import Activity, CurrentState, ShortTermDesire


def schedule_block_id(now: float, grid_minutes: int) -> str:
    """把时间戳映射到日程网格标签。"""
    block_index = int(now % 86400) // 60 // grid_minutes
    return format_time_label(block_index, grid_minutes, 0.0)


def _empty_progress() -> dict[str, Any]:
    return {"desire_id": None, "goal": None, "correlation_id": None}


def _harvest_task_exception(task: asyncio.Task[None]) -> None:
    if not task.cancelled():
        task.exception()


class ActivityStarter:
    """选择或恢复下一项活动，并创建唯一的后台执行任务。"""

    def __init__(
        self,
        store: ActivityStore,
        material_store: MaterialStore,
        desire: DesireFacade,
        get_state: Callable[[], Awaitable[CurrentState]],
        execute: Callable[[Activity], Coroutine[Any, Any, None]],
        config: ActivityConfig,
        exploration_config: ExplorationConfig,
        now: Callable[[], float],
    ) -> None:
        self._store = store
        self._material_store = material_store
        self._desire = desire
        self._get_state = get_state
        self._execute = execute
        self._config = config
        self._exploration_config = exploration_config
        self._now = now
        self._lock = asyncio.Lock()

    def select_activity(
        self, desires: list[ShortTermDesire], state: CurrentState
    ) -> Activity | None:
        """从已排序欲望中选择活动；纯互动欲望不占日程块。"""
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
            target = None
        now = self._now()
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
            schedule_block_id=schedule_block_id(now, self._config.grid_minutes),
            status=ActivityStatus.PENDING,
            progress=progress,
            started_at=now,
        )

    def default_activity(self, state: CurrentState) -> Activity:
        """无可消费欲望时，根据精力选择观察或发呆反思。"""
        activity_type = (
            ActivityType.IDLE_REFLECTION
            if state.energy < ENERGY_REST_THRESHOLD
            else ActivityType.OBSERVE_USER
        )
        now = self._now()
        return Activity(
            id=str(uuid.uuid4()),
            type=activity_type,
            schedule_block_id=schedule_block_id(now, self._config.grid_minutes),
            status=ActivityStatus.PENDING,
            progress=_empty_progress(),
            started_at=now,
        )

    async def start_next_if_idle(
        self, current_task: asyncio.Task[None] | None
    ) -> asyncio.Task[None] | None:
        """已有活动时保持不动，否则恢复或创建下一项活动。"""
        async with self._lock:
            if current_task is not None and not current_task.done():
                return current_task
            current = await self._store.get_current()
            if current is not None and current.status is ActivityStatus.RUNNING:
                return current_task
            block_id = schedule_block_id(self._now(), self._config.grid_minutes)
            resumed = await self._store.get_paused_in_block(block_id)
            if resumed is not None:
                if resumed.type is ActivityType.READING:
                    source = resumed.progress.get("source")
                    if isinstance(source, str):
                        material = await self._material_store.get_by_path(source)
                        if material is not None:
                            resumed.progress["read_chars"] = material.read_chars
                            resumed.progress["total_chars"] = material.total_chars
                resumed.ended_at = None
                return self._create_task(resumed)

            desires = await self._desire.get_pending()
            values = (await self._desire.get_all()).values
            state = await self._get_state()
            activity = self.select_activity(rank_desires(desires, values), state)
            if activity is None:
                activity = self.default_activity(state)
            if activity.type is ActivityType.READING:
                goal = cast(dict[str, Any] | None, activity.progress.get("goal"))
                topic = goal.get("topic") if goal is not None else None
                material = None
                if isinstance(topic, str) and topic:
                    material = await self._material_store.find_by_topic(topic)
                if material is None:
                    material = await self._material_store.next_readable()
                if material is not None:
                    activity.progress.update(
                        {
                            "source": material.path,
                            "filename": material.filename,
                            "description": material.filename,
                            "read_chars": material.read_chars,
                            "total_chars": material.total_chars,
                        }
                    )
                else:
                    last = await self._store.get_last_exploration()
                    if (
                        isinstance(topic, str)
                        and topic
                        and should_explore(
                            last,
                            self._exploration_config.rate_limit_hours,
                            self._now(),
                        )
                    ):
                        activity.type = ActivityType.FREE_EXPLORATION
                    else:
                        activity = self.default_activity(state)
            await self._store.insert(activity)
            return self._create_task(activity)

    def _create_task(self, activity: Activity) -> asyncio.Task[None]:
        task = asyncio.create_task(self._execute(activity))
        task.add_done_callback(_harvest_task_exception)
        return task
