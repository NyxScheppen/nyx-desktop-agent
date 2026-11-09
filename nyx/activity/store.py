import json
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any, cast

import aiosqlite

from nyx.db import Database
from nyx.enums import (
    ActivityStatus,
    ActivityType,
    AssignedTaskStatus,
    AssignedTaskType,
)
from nyx.types import Activity, AssignedTask

_COLS = "id, type, schedule_block_id, status, progress, started_at, ended_at"
_TASK_COLS = (
    "id, type, status, url, book_id, target_paragraph, checkpoint, error, "
    "created_at, updated_at"
)


class ActivityStore:
    """活动、委派任务及任务正文快照 CRUD。

    所有读写都 `async with self._db.lock:` 串行化（同 05/07/11）。
    """

    def __init__(self, db: Database) -> None:
        self._db = db

    @property
    def db(self) -> Database:
        """Return the shared database for local transaction orchestration."""
        return self._db

    @asynccontextmanager
    async def _operation(self) -> AsyncGenerator[bool, None]:
        """Reuse the caller's transaction instead of acquiring the lock twice."""
        if self._db.in_transaction:
            yield False
            return
        async with self._db.lock:
            yield True

    async def insert(self, activity: Activity) -> None:
        if self._db.in_transaction:
            await self._db.conn.execute(
                "INSERT INTO activity (id, type, schedule_block_id, progress, "
                "status, started_at, ended_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    activity.id,
                    activity.type.value,
                    activity.schedule_block_id,
                    json.dumps(activity.progress),
                    activity.status.value,
                    activity.started_at,
                    activity.ended_at,
                ),
            )
            return
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
        """当前活动（running），取最新一条。"""
        async with self._operation():
            cursor = await self._db.conn.execute(
                f"SELECT {_COLS} FROM activity WHERE status = 'running' "
                "ORDER BY started_at DESC LIMIT 1",
            )
            row = await cursor.fetchone()
        return _row_to_activity(row) if row is not None else None

    async def list_running(self) -> list[Activity]:
        """所有 RUNNING 活动，按 started_at 升序（启动恢复清理用）。"""
        async with self._db.lock:
            cursor = await self._db.conn.execute(
                f"SELECT {_COLS} FROM activity WHERE status = 'running' "
                "ORDER BY started_at ASC"
            )
            rows = await cursor.fetchall()
        return [_row_to_activity(r) for r in rows]

    async def list_unfinished(self) -> list[Activity]:
        """Return stale startup candidates in creation order."""
        async with self._operation():
            cursor = await self._db.conn.execute(
                f"SELECT {_COLS} FROM activity "
                "WHERE status IN ('pending', 'running') "
                "ORDER BY started_at ASC"
            )
            rows = await cursor.fetchall()
        return [_row_to_activity(row) for row in rows]

    async def get_paused_in_block(
        self, schedule_block_id: str, day_start: float, day_end: float
    ) -> Activity | None:
        """当前日程块内最新一条 PAUSED 记录（供恢复）；无则 None。"""
        async with self._db.lock:
            cursor = await self._db.conn.execute(
                f"SELECT {_COLS} FROM activity WHERE status = 'paused' "
                "AND schedule_block_id = ? AND started_at >= ? AND started_at < ? "
                "ORDER BY started_at DESC LIMIT 1",
                (schedule_block_id, day_start, day_end),
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

    async def list_exploration_history(self) -> list[dict[str, Any]]:
        """Project actual outcomes, including failed runs absent from activity memory.

        Keep every completed question for dedup; callers bound prompt samples.
        Raw pages and tool payloads never enter the generation context.
        """
        async with self._operation():
            cursor = await self._db.conn.execute(
                f"SELECT {_COLS} FROM activity WHERE type = 'free_exploration' "
                "AND status IN ('completed', 'incomplete') "
                "ORDER BY started_at DESC"
            )
            rows = await cursor.fetchall()
        history: list[dict[str, Any]] = []
        for row in rows:
            activity = _row_to_activity(row)
            progress = activity.progress
            raw = progress.get("exploration", {})
            exploration = cast(dict[str, Any], raw) if isinstance(raw, dict) else {}
            raw_result = progress.get("result", exploration.get("judged", {}))
            result = (
                cast(dict[str, Any], raw_result)
                if isinstance(raw_result, dict) else {}
            )
            raw_goal = progress.get("goal", {})
            goal = cast(dict[str, Any], raw_goal) if isinstance(raw_goal, dict) else {}
            history.append({
                "activity_id": activity.id,
                "desire_id": progress.get("desire_id"),
                "status": activity.status.value,
                "started_at": activity.started_at,
                "seed_topic": goal.get("topic", exploration.get("topic", "")),
                "question": exploration.get("initial_question")
                or progress.get("intent") or progress.get("description")
                or exploration.get("topic", ""),
                "summary": str(result.get("summary", ""))[:1500],
                "core_discovery": str(result.get("core_discovery", ""))[:1500],
                "failure_reason": exploration.get("failure_reason")
                or result.get("failure_reason") or progress.get("error") or "未知",
                "unresolved_questions": result.get("unresolved_questions")
                or exploration.get("unresolved_questions", []),
            })
        return history

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

    async def list_results(
        self,
        limit: int,
        offset: int = 0,
        activity_type: ActivityType | None = None,
    ) -> list[Activity]:
        """List completed output activities newest first, with optional filtering."""
        async with self._db.lock:
            if activity_type is None:
                cursor = await self._db.conn.execute(
                    f"SELECT {_COLS} FROM activity "
                    "WHERE status = 'completed' AND type IN "
                    "('reading', 'free_exploration', 'creation') "
                    "ORDER BY ended_at DESC LIMIT ? OFFSET ?",
                    (limit, offset),
                )
            else:
                cursor = await self._db.conn.execute(
                    f"SELECT {_COLS} FROM activity "
                    "WHERE status = 'completed' AND type = ? "
                    "ORDER BY ended_at DESC LIMIT ? OFFSET ?",
                    (activity_type.value, limit, offset),
                )
            rows = await cursor.fetchall()
        return [_row_to_activity(r) for r in rows]

    async def update(self, activity: Activity) -> None:
        if self._db.in_transaction:
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
            return
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

    async def create_task(self, task: AssignedTask) -> AssignedTask:
        """Insert a task, returning an equivalent active task when one exists."""
        async with self._db.lock:
            if task.type in (AssignedTaskType.WEB, AssignedTaskType.BROWSER_SURVEY):
                cursor = await self._db.conn.execute(
                    f"SELECT {_TASK_COLS} FROM assigned_task "
                    "WHERE type = ? AND url = ? "
                    "AND status IN ('pending', 'running') "
                    "ORDER BY created_at ASC LIMIT 1",
                    (task.type.value, task.url),
                )
            elif task.type is AssignedTaskType.BOOK:
                cursor = await self._db.conn.execute(
                    f"SELECT {_TASK_COLS} FROM assigned_task "
                    "WHERE type = 'book' AND book_id = ? AND target_paragraph = ? "
                    "AND status IN ('pending', 'running') "
                    "ORDER BY created_at ASC LIMIT 1",
                    (task.book_id, task.target_paragraph),
                )
            else:
                cursor = await self._db.conn.execute(
                    f"SELECT {_TASK_COLS} FROM assigned_task "
                    "WHERE type = ? AND status IN ('pending', 'running') "
                    "ORDER BY created_at ASC",
                    (task.type.value,),
                )
                for row in await cursor.fetchall():
                    existing_task = _row_to_task(row)
                    if (
                        existing_task.checkpoint.get("description")
                        == task.checkpoint.get("description")
                    ):
                        return existing_task
                cursor = None
            existing = await cursor.fetchone() if cursor is not None else None
            if existing is not None:
                return _row_to_task(existing)
            await self._db.conn.execute(
                "INSERT INTO assigned_task "
                "(id, type, status, url, book_id, target_paragraph, checkpoint, "
                "error, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    task.id,
                    task.type.value,
                    task.status.value,
                    task.url,
                    task.book_id,
                    task.target_paragraph,
                    json.dumps(task.checkpoint, ensure_ascii=False),
                    task.error,
                    task.created_at,
                    task.updated_at,
                ),
            )
            await self._db.conn.commit()
        return task

    async def get_task(self, task_id: str) -> AssignedTask | None:
        async with self._db.lock:
            cursor = await self._db.conn.execute(
                f"SELECT {_TASK_COLS} FROM assigned_task WHERE id = ?", (task_id,)
            )
            row = await cursor.fetchone()
        return _row_to_task(row) if row is not None else None

    async def list_tasks(self, limit: int = 100) -> list[AssignedTask]:
        async with self._db.lock:
            cursor = await self._db.conn.execute(
                f"SELECT {_TASK_COLS} FROM assigned_task "
                "ORDER BY created_at DESC LIMIT ?",
                (limit,),
            )
            rows = await cursor.fetchall()
        return [_row_to_task(row) for row in rows]

    async def get_next_task(self) -> AssignedTask | None:
        async with self._db.lock:
            cursor = await self._db.conn.execute(
                f"SELECT {_TASK_COLS} FROM assigned_task WHERE status = 'pending' "
                "ORDER BY created_at ASC LIMIT 1"
            )
            row = await cursor.fetchone()
        return _row_to_task(row) if row is not None else None

    async def claim_task_for_activity(self, task_id: str, now: float) -> bool:
        """Claim a pending task inside the caller's activity-start transaction."""
        cursor = await self._db.conn.execute(
            "UPDATE assigned_task SET status = 'running', error = NULL, "
            "updated_at = ? WHERE id = ? AND status = 'pending'",
            (now, task_id),
        )
        return cursor.rowcount == 1

    async def get_task_source(self, task_id: str) -> str | None:
        """Read only this task's immutable source, without loading task lists."""
        async with self._operation():
            cursor = await self._db.conn.execute(
                "SELECT content FROM assigned_task_source WHERE task_id = ?",
                (task_id,),
            )
            row = await cursor.fetchone()
        return row["content"] if row is not None else None

    async def save_task_source(self, task_id: str, content: str) -> str:
        """Keep the first committed source so replay cannot change its evidence."""
        if len(content) > 200_000:
            raise ValueError("网页正文快照超出 200000 字符")
        async with self._operation() as should_commit:
            try:
                await self._db.conn.execute(
                    "INSERT INTO assigned_task_source(task_id, content) VALUES (?, ?) "
                    "ON CONFLICT(task_id) DO NOTHING",
                    (task_id, content),
                )
                cursor = await self._db.conn.execute(
                    "SELECT content FROM assigned_task_source WHERE task_id = ?",
                    (task_id,),
                )
                row = await cursor.fetchone()
                assert row is not None
                if should_commit:
                    await self._db.conn.commit()
            except BaseException:
                if should_commit:
                    await self._db.conn.rollback()
                raise
        return row["content"]

    async def save_task(self, task: AssignedTask) -> None:
        async with self._operation() as should_commit:
            await self._db.conn.execute(
                "UPDATE assigned_task SET status = ?, checkpoint = ?, error = ?, "
                "updated_at = ? WHERE id = ?",
                (
                    task.status.value,
                    json.dumps(task.checkpoint, ensure_ascii=False),
                    task.error,
                    task.updated_at,
                    task.id,
                ),
            )
            if should_commit:
                await self._db.conn.commit()

    async def set_task_status(
        self,
        task_id: str,
        status: AssignedTaskStatus,
        now: float,
        error: str | None = None,
    ) -> AssignedTask | None:
        async with self._operation() as should_commit:
            await self._db.conn.execute(
                "UPDATE assigned_task SET status = ?, error = ?, updated_at = ? "
                "WHERE id = ?",
                (status.value, error, now, task_id),
            )
            if should_commit:
                await self._db.conn.commit()
            cursor = await self._db.conn.execute(
                f"SELECT {_TASK_COLS} FROM assigned_task WHERE id = ?", (task_id,)
            )
            row = await cursor.fetchone()
        return _row_to_task(row) if row is not None else None

    async def list_running_tasks(self) -> list[AssignedTask]:
        """List tasks left RUNNING for startup or shutdown recovery."""
        async with self._operation():
            cursor = await self._db.conn.execute(
                f"SELECT {_TASK_COLS} FROM assigned_task WHERE status = 'running' "
                "ORDER BY created_at ASC"
            )
            rows = await cursor.fetchall()
        return [_row_to_task(row) for row in rows]

    async def get_latest_activity_for_task(self, task_id: str) -> Activity | None:
        """Return the newest activity durably linked to an assigned task."""
        async with self._operation():
            cursor = await self._db.conn.execute(
                f"SELECT {_COLS} FROM activity "
                "WHERE json_extract(progress, '$.task_id') = ? "
                "ORDER BY started_at DESC LIMIT 1",
                (task_id,),
            )
            row = await cursor.fetchone()
        return _row_to_activity(row) if row is not None else None


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


def _row_to_task(row: aiosqlite.Row) -> AssignedTask:
    return AssignedTask(
        id=row["id"],
        type=AssignedTaskType(row["type"]),
        status=AssignedTaskStatus(row["status"]),
        url=row["url"],
        book_id=row["book_id"],
        target_paragraph=row["target_paragraph"],
        checkpoint=json.loads(row["checkpoint"]),
        error=row["error"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )
