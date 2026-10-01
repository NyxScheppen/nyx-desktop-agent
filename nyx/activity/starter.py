import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable, Coroutine
from datetime import datetime, timedelta
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, cast

from nyx.activity.exploration import should_explore
from nyx.activity.scheduler import (
    build_schedule,
    desire_to_activity,
    format_time_label,
    rank_desires,
)
from nyx.activity.store import ActivityStore
from nyx.config import ActivityConfig, ExplorationConfig
from nyx.desire.facade import DesireFacade
from nyx.enums import (
    ActivityStatus,
    ActivityType,
    AssignedTaskType,
    DesireType,
)
from nyx.inner_life.emotion import ENERGY_REST_THRESHOLD
from nyx.types import Activity, Book, CurrentState, ShortTermDesire

_READING_MATCH_THRESHOLD = 0.6
_logger = logging.getLogger(__name__)


def _normalized_title(value: str) -> str:
    stem = Path(value.strip()).stem
    return "".join(char for char in stem.casefold() if char.isalnum())


def _reading_match_score(topic: str, candidate: str) -> float:
    query = _normalized_title(topic)
    name = _normalized_title(candidate)
    if not query or not name:
        return 0.0
    if query == name:
        return 1.0
    if query in name or name in query:
        return 0.9 + 0.09 * min(len(query), len(name)) / max(len(query), len(name))
    return SequenceMatcher(None, query, name).ratio()


def best_book_match(topic: str, books: list[Book]) -> Book | None:
    """Return the best fuzzy title or filename match from readable EPUB books."""
    ranked: list[tuple[float, float, Book]] = []
    for book in books:
        score = max(
            _reading_match_score(topic, book.title),
            _reading_match_score(topic, book.filename),
        )
        if score >= _READING_MATCH_THRESHOLD:
            ranked.append((score, book.created_at, book))
    if not ranked:
        return None
    return max(ranked, key=lambda item: (item[0], item[1]))[2]


def schedule_block_id(now: float, grid_minutes: int) -> str:
    """把时间戳映射到日程网格标签。"""
    local_now = datetime.fromtimestamp(now)
    block_index = (local_now.hour * 60 + local_now.minute) // grid_minutes
    return format_time_label(block_index, grid_minutes, 0.0)


def local_day_bounds(now: float) -> tuple[float, float]:
    """Return local calendar-day bounds for paused-activity recovery."""
    local_now = datetime.fromtimestamp(now)
    start = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=1)
    return start.timestamp(), end.timestamp()


def _empty_progress() -> dict[str, Any]:
    return {"desire_id": None, "goal": None, "correlation_id": None}


def _harvest_task_exception(task: asyncio.Task[None]) -> None:
    if task.cancelled():
        return
    error = task.exception()
    if error is not None:
        _logger.error(
            "活动后台任务失败",
            exc_info=(type(error), error, error.__traceback__),
        )


class ActivityStarter:
    """选择或恢复下一项活动，并创建唯一的后台执行任务。"""

    def __init__(
        self,
        store: ActivityStore,
        desire: DesireFacade,
        get_state: Callable[[], Awaitable[CurrentState]],
        execute: Callable[[Activity], Coroutine[Any, Any, None]],
        config: ActivityConfig,
        exploration_config: ExplorationConfig,
        now: Callable[[], float],
        start: Callable[[Activity, bool], Awaitable[bool]],
        list_reader_books: Callable[[], Awaitable[list[Book]]] | None = None,
    ) -> None:
        self._store = store
        self._desire = desire
        self._get_state = get_state
        self._execute = execute
        self._config = config
        self._exploration_config = exploration_config
        self._now = now
        self._list_reader_books = list_reader_books
        self._start = start
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
            progress["parent_long_term_id"] = target.parent_long_term_id
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
            now = self._now()
            block_id = schedule_block_id(now, self._config.grid_minutes)
            day_start, day_end = local_day_bounds(now)
            resumed = await self._store.get_paused_in_block(
                block_id, day_start, day_end
            )
            if resumed is not None:
                if await self._commit_start(resumed, is_new=False):
                    return self._create_task(resumed)

            task = await self._store.get_next_task()
            state = await self._get_state()
            if task is not None:
                now = self._now()
                if state.energy < ENERGY_REST_THRESHOLD:
                    rest = Activity(
                        id=str(uuid.uuid4()),
                        type=ActivityType.REST,
                        schedule_block_id=schedule_block_id(
                            now, self._config.grid_minutes
                        ),
                        status=ActivityStatus.PENDING,
                        progress=_empty_progress(),
                        started_at=now,
                    )
                    if not await self._commit_start(rest, is_new=True):
                        return None
                    return self._create_task(rest)
                progress = _empty_progress()
                progress.update(
                    {
                        "task_id": task.id,
                        "correlation_id": task.id,
                        "assigned_task_type": task.type.value,
                        "description": task.url or task.book_id,
                    }
                )
                if task.type is AssignedTaskType.WEB:
                    progress["url"] = task.url
                else:
                    progress["book_id"] = task.book_id
                    progress["target_paragraph"] = task.target_paragraph
                activity = Activity(
                    id=str(uuid.uuid4()),
                    type=ActivityType.READING,
                    schedule_block_id=schedule_block_id(
                        now, self._config.grid_minutes
                    ),
                    status=ActivityStatus.PENDING,
                    progress=progress,
                    started_at=now,
                )
                if not await self._commit_start(activity, is_new=True):
                    return None
                return self._create_task(activity)

            desires = await self._desire.get_pending()
            values = (await self._desire.get_all()).values
            activity = self.select_activity(rank_desires(desires, values), state)
            if activity is None:
                activity = self.default_activity(state)
            if activity.type is ActivityType.READING:
                goal = cast(dict[str, Any] | None, activity.progress.get("goal"))
                topic = goal.get("topic") if goal is not None else None
                book = None
                if isinstance(topic, str) and topic:
                    books = (
                        await self._list_reader_books()
                        if self._list_reader_books is not None
                        else []
                    )
                    book = best_book_match(topic, books)
                if book is not None:
                    activity.progress.update(
                        {
                            "book_id": book.id,
                            "filename": book.filename,
                            "description": book.title,
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
            if not await self._commit_start(activity, is_new=True):
                return None
            return self._create_task(activity)

    async def _commit_start(self, activity: Activity, *, is_new: bool) -> bool:
        return await self._start(activity, is_new)

    def _create_task(self, activity: Activity) -> asyncio.Task[None]:
        task = asyncio.create_task(self._execute(activity))
        task.add_done_callback(_harvest_task_exception)
        return task
