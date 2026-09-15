import json
import re
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import aiosqlite

from nyx.db import Database
from nyx.desire.value import apply_pressure, default_value
from nyx.enums import DesireStatus, DesireType, GoalAction
from nyx.types import DesireValue, Goal, LongTermDesire, ShortTermDesire

_STD_COLS = (
    "id, created_at, type, strength, description, goal, retry_count, "
    "status, goal_progress"
)
_VALUE_COLS = "type, value, expression_weight, suppression_threshold, updated_at"
_LT_COLS = (
    "id, created_at, type, name, description, strength, progress, "
    "subtopics, linked_values, name_normalized"
)


class DesireStore:
    """short_term_desire / desire_value / long_term_desire 三表 CRUD
    + 行↔dataclass 序列化。

    db 由组合根注入（同所有 store 共享一个 conn+lock）。每个方法一个
    `async with db.lock` 的 SQL 块，不跨方法嵌套（asyncio.Lock 不可重入）。
    """

    def __init__(self, db: Database) -> None:
        self._db = db

    @property
    def db(self) -> Database:
        """Return the shared database for local transaction orchestration."""
        return self._db

    @asynccontextmanager
    async def _operation(self) -> AsyncGenerator[bool, None]:
        """Yield whether this method owns the commit for its SQL block."""
        if self._db.in_transaction:
            yield False
            return
        async with self._db.lock:
            yield True

    # —— short_term_desire ——

    async def add_desire(self, desire: ShortTermDesire) -> None:
        async with self._operation() as should_commit:
            await self._db.conn.execute(
                f"INSERT INTO short_term_desire ({_STD_COLS}) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                _std_row(desire),
            )
            if should_commit:
                await self._db.conn.commit()

    async def get_desire(self, desire_id: str) -> ShortTermDesire | None:
        async with self._operation():
            cursor = await self._db.conn.execute(
                f"SELECT {_STD_COLS} FROM short_term_desire WHERE id = ?", (desire_id,),
            )
            row = await cursor.fetchone()
        return _row_to_std(row) if row is not None else None

    async def list_pending(self) -> list[ShortTermDesire]:
        async with self._operation():
            cursor = await self._db.conn.execute(
                f"SELECT {_STD_COLS} FROM short_term_desire "
                "WHERE status = ? ORDER BY created_at ASC",
                (DesireStatus.PENDING.value,),
            )
            rows = await cursor.fetchall()
        return [_row_to_std(r) for r in rows]

    async def list_suppressed(self) -> list[ShortTermDesire]:
        async with self._operation():
            cursor = await self._db.conn.execute(
                f"SELECT {_STD_COLS} FROM short_term_desire "
                "WHERE status = ? ORDER BY created_at ASC",
                (DesireStatus.SUPPRESSED.value,),
            )
            rows = await cursor.fetchall()
        return [_row_to_std(r) for r in rows]

    async def list_short_term(self) -> list[ShortTermDesire]:
        """全部短期欲望（含 satisfied/expired 历史），供 /api/desires
        全量快照；最新在前。"""
        async with self._operation():
            cursor = await self._db.conn.execute(
                f"SELECT {_STD_COLS} FROM short_term_desire ORDER BY created_at DESC"
            )
            rows = await cursor.fetchall()
        return [_row_to_std(r) for r in rows]

    async def update_desire(self, desire: ShortTermDesire) -> None:
        async with self._operation() as should_commit:
            await self._db.conn.execute(
                "UPDATE short_term_desire SET type = ?, strength = ?, description = ?, "
                "goal = ?, retry_count = ?, status = ?, goal_progress = ? WHERE id = ?",
                (
                    desire.type.value, desire.strength, desire.description,
                    _goal_json(desire.goal), desire.retry_count, desire.status.value,
                    desire.goal_progress, desire.id,
                ),
            )
            if should_commit:
                await self._db.conn.commit()

    # —— desire_value ——

    async def get_value(self, type_: DesireType) -> DesireValue | None:
        async with self._operation():
            cursor = await self._db.conn.execute(
                f"SELECT {_VALUE_COLS} FROM desire_value WHERE type = ?",
                (type_.value,),
            )
            row = await cursor.fetchone()
        return _row_to_value(row) if row is not None else None

    async def list_values(self) -> list[DesireValue]:
        async with self._operation():
            cursor = await self._db.conn.execute(
                f"SELECT {_VALUE_COLS} FROM desire_value"
            )
            rows = await cursor.fetchall()
        return [_row_to_value(r) for r in rows]

    async def upsert_value(self, dv: DesireValue) -> None:
        async with self._operation() as should_commit:
            await self._db.conn.execute(
                f"INSERT INTO desire_value ({_VALUE_COLS}) VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(type) DO UPDATE SET value = excluded.value, "
                "expression_weight = excluded.expression_weight, "
                "suppression_threshold = excluded.suppression_threshold, "
                "updated_at = excluded.updated_at",
                (
                    dv.type.value, dv.value, dv.expression_weight,
                    dv.suppression_threshold, dv.updated_at,
                ),
            )
            if should_commit:
                await self._db.conn.commit()

    async def apply_value_delta(
        self, type_: DesireType, delta: float, now: float
    ) -> DesireValue:
        """Atomically apply pressure to one desire value and return its new row."""
        async with self._operation() as should_commit:
            cursor = await self._db.conn.execute(
                f"SELECT {_VALUE_COLS} FROM desire_value WHERE type = ?",
                (type_.value,),
            )
            row = await cursor.fetchone()
            if row is None:
                base = default_value(type_)
                value = base.value
                expression_weight = base.expression_weight
                suppression_threshold = base.suppression_threshold
            else:
                value = float(row["value"])
                expression_weight = float(row["expression_weight"])
                suppression_threshold = float(row["suppression_threshold"])
            value = apply_pressure(value, delta)
            updated = DesireValue(
                type=type_,
                value=value,
                expression_weight=expression_weight,
                suppression_threshold=suppression_threshold,
                updated_at=max(now, float(row["updated_at"]) + 1e-9)
                if row is not None
                else now,
            )
            await self._db.conn.execute(
                f"INSERT INTO desire_value ({_VALUE_COLS}) VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(type) DO UPDATE SET value = excluded.value, "
                "updated_at = excluded.updated_at",
                (
                    type_.value,
                    updated.value,
                    updated.expression_weight,
                    updated.suppression_threshold,
                    updated.updated_at,
                ),
            )
            if should_commit:
                await self._db.conn.commit()
        return updated

    async def reset_value_if_unchanged(
        self, type_: DesireType, expected_updated_at: float, now: float
    ) -> bool:
        """Reset a value only when no concurrent pressure changed its timestamp."""
        async with self._operation() as should_commit:
            cursor = await self._db.conn.execute(
                "UPDATE desire_value SET value = 0.0, updated_at = ? "
                "WHERE type = ? AND updated_at = ?",
                (now, type_.value, expected_updated_at),
            )
            if should_commit:
                await self._db.conn.commit()
        return cursor.rowcount == 1

    async def claim_for_activity(self, desire_id: str) -> bool:
        """Atomically claim one pending desire for an activity."""
        async with self._operation() as should_commit:
            cursor = await self._db.conn.execute(
                "UPDATE short_term_desire SET status = ? "
                "WHERE id = ? AND status = ?",
                (
                    DesireStatus.ACTIVE.value,
                    desire_id,
                    DesireStatus.PENDING.value,
                ),
            )
            if should_commit:
                await self._db.conn.commit()
        return cursor.rowcount == 1

    async def trim_pending(
        self, capacity: int, expression_weights: dict[DesireType, float]
    ) -> list[str]:
        """Keep the highest-expression pending desires within capacity."""
        async with self._operation() as should_commit:
            cursor = await self._db.conn.execute(
                f"SELECT {_STD_COLS} FROM short_term_desire "
                "WHERE status = ? ORDER BY created_at ASC, id ASC",
                (DesireStatus.PENDING.value,),
            )
            rows = await cursor.fetchall()
            ranked = sorted(
                (_row_to_std(row) for row in rows),
                key=lambda desire: (
                    -expression_weights.get(desire.type, 0.0),
                    desire.created_at,
                    desire.id,
                ),
            )
            removed = ranked[capacity:]
            for desire in removed:
                await self._db.conn.execute(
                    "DELETE FROM short_term_desire WHERE id = ?",
                    (desire.id,),
                )
            if should_commit:
                await self._db.conn.commit()
        return [desire.id for desire in removed]

    # —— long_term_desire ——

    async def insert_long_term(self, desire: LongTermDesire) -> None:
        async with self._operation() as should_commit:
            await self._db.conn.execute(
                f"INSERT INTO long_term_desire ({_LT_COLS}) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                _lt_row(desire),
            )
            if should_commit:
                await self._db.conn.commit()

    async def insert_long_term_if_available(
        self, desire: LongTermDesire, capacity: int
    ) -> bool:
        """Insert only when capacity and normalized-name uniqueness still hold."""
        async with self._operation() as should_commit:
            cursor = await self._db.conn.execute(
                "SELECT COUNT(*) AS count FROM long_term_desire"
            )
            row = await cursor.fetchone()
            count = int(row["count"]) if row is not None else 0
            if count >= capacity:
                return False
            cursor = await self._db.conn.execute(
                f"INSERT OR IGNORE INTO long_term_desire ({_LT_COLS}) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                _lt_row(desire),
            )
            if should_commit:
                await self._db.conn.commit()
        return cursor.rowcount == 1

    async def insert_generation_attempt(
        self,
        attempt_id: str,
        type_: DesireType,
        created_at: float,
        peak_value: float,
        seed: str | None,
        output_content: str,
    ) -> None:
        async with self._operation() as should_commit:
            await self._db.conn.execute(
                """INSERT INTO desire_generation_attempt
                (id, type, created_at, peak_value, seed, output_content)
                VALUES (?, ?, ?, ?, ?, ?)""",
                (attempt_id, type_.value, created_at, peak_value, seed, output_content),
            )
            if should_commit:
                await self._db.conn.commit()

    async def get_generation_attempt(
        self, type_: DesireType
    ) -> tuple[str, float, float, str | None, str] | None:
        async with self._operation():
            cursor = await self._db.conn.execute(
                """SELECT id, created_at, peak_value, seed, output_content
                FROM desire_generation_attempt
                WHERE type = ? ORDER BY created_at ASC, id ASC LIMIT 1""",
                (type_.value,),
            )
            row = await cursor.fetchone()
        if row is None:
            return None
        return (
            str(row["id"]),
            float(row["created_at"]),
            float(row["peak_value"]),
            row["seed"],
            str(row["output_content"]),
        )

    async def delete_generation_attempt(self, attempt_id: str) -> None:
        async with self._operation() as should_commit:
            await self._db.conn.execute(
                "DELETE FROM desire_generation_attempt WHERE id = ?",
                (attempt_id,),
            )
            if should_commit:
                await self._db.conn.commit()

    async def list_long_term(self) -> list[LongTermDesire]:
        async with self._operation():
            cursor = await self._db.conn.execute(
                f"SELECT {_LT_COLS} FROM long_term_desire"
            )
            rows = await cursor.fetchall()
        return [_row_to_lt(r) for r in rows]

    async def update_long_term(self, desire: LongTermDesire) -> None:
        async with self._operation() as should_commit:
            await self._db.conn.execute(
                "UPDATE long_term_desire SET type = ?, name = ?, description = ?, "
                "strength = ?, progress = ?, subtopics = ?, "
                "linked_values = ? WHERE id = ?",
                (
                    desire.type.value, desire.name, desire.description,
                    desire.strength, desire.progress, json.dumps(desire.subtopics),
                    json.dumps(desire.linked_values), desire.id,
                ),
            )
            if should_commit:
                await self._db.conn.commit()


def _std_row(
    d: ShortTermDesire
) -> tuple[str, float, str, float, str, str | None, int, str, int]:
    return (
        d.id, d.created_at, d.type.value, d.strength, d.description,
        _goal_json(d.goal), d.retry_count, d.status.value, d.goal_progress,
    )


def _goal_json(g: Goal | None) -> str | None:
    if g is None:
        return None
    return json.dumps({"action": g.action.value, "count": g.count, "topic": g.topic})


def _row_to_std(row: aiosqlite.Row) -> ShortTermDesire:
    return ShortTermDesire(
        id=row["id"],
        created_at=row["created_at"],
        type=DesireType(row["type"]),
        strength=row["strength"],
        description=row["description"],
        goal=_parse_goal(row["goal"]),
        retry_count=row["retry_count"],
        status=DesireStatus(row["status"]),
        goal_progress=row["goal_progress"],
    )


def _parse_goal(raw: str | None) -> Goal | None:
    if raw is None:
        return None
    data = json.loads(raw)
    return Goal(
        action=GoalAction(data["action"]),
        count=data["count"],
        topic=data.get("topic"),
    )


def _row_to_value(row: aiosqlite.Row) -> DesireValue:
    return DesireValue(
        type=DesireType(row["type"]),
        value=row["value"],
        expression_weight=row["expression_weight"],
        suppression_threshold=row["suppression_threshold"],
        updated_at=row["updated_at"],
    )


def _lt_row(
    d: LongTermDesire
) -> tuple[str, float, str, str, str, float, float, str, str, str]:
    return (
        d.id, d.created_at, d.type.value, d.name, d.description,
        d.strength, d.progress, json.dumps(d.subtopics), json.dumps(d.linked_values),
        normalize_name(d.name),
    )


def _row_to_lt(row: aiosqlite.Row) -> LongTermDesire:
    return LongTermDesire(
        id=row["id"],
        created_at=row["created_at"],
        type=DesireType(row["type"]),
        name=row["name"],
        description=row["description"],
        strength=row["strength"],
        progress=row["progress"],
        subtopics=json.loads(row["subtopics"]),
        linked_values=json.loads(row["linked_values"]),
    )


def normalize_name(name: str) -> str:
    """Normalize a long-term desire name for deterministic uniqueness."""
    return re.sub(r"\s+", " ", name.strip()).casefold()
