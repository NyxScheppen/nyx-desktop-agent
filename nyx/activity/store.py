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
        """当前活动（running），取最新一条。"""
        async with self._db.lock:
            cursor = await self._db.conn.execute(
                f"SELECT {_COLS} FROM activity WHERE status = 'running' "
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
