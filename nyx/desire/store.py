import json

import aiosqlite

from nyx.db import Database
from nyx.enums import DesireStatus, DesireType, GoalAction
from nyx.types import DesireValue, Goal, LongTermDesire, ShortTermDesire

_STD_COLS = "id, created_at, type, strength, description, goal, retry_count, status"
_VALUE_COLS = "type, value, expression_weight, suppression_threshold, updated_at"
_LT_COLS = (
    "id, created_at, type, name, description, strength, progress, "
    "subtopics, linked_values"
)


class DesireStore:
    """short_term_desire / desire_value / long_term_desire 三表 CRUD
    + 行↔dataclass 序列化。

    db 由组合根注入（同所有 store 共享一个 conn+lock）。每个方法一个
    `async with db.lock` 的 SQL 块，不跨方法嵌套（asyncio.Lock 不可重入）。
    """

    def __init__(self, db: Database) -> None:
        self._db = db

    # —— short_term_desire ——

    async def add_desire(self, desire: ShortTermDesire) -> None:
        async with self._db.lock:
            await self._db.conn.execute(
                f"INSERT INTO short_term_desire ({_STD_COLS}) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                _std_row(desire),
            )
            await self._db.conn.commit()

    async def get_desire(self, desire_id: str) -> ShortTermDesire | None:
        async with self._db.lock:
            cursor = await self._db.conn.execute(
                f"SELECT {_STD_COLS} FROM short_term_desire WHERE id = ?", (desire_id,),
            )
            row = await cursor.fetchone()
        return _row_to_std(row) if row is not None else None

    async def list_pending(self) -> list[ShortTermDesire]:
        async with self._db.lock:
            cursor = await self._db.conn.execute(
                f"SELECT {_STD_COLS} FROM short_term_desire "
                "WHERE status IN (?, ?) ORDER BY created_at ASC",
                (DesireStatus.PENDING.value, DesireStatus.ACTIVE.value),
            )
            rows = await cursor.fetchall()
        return [_row_to_std(r) for r in rows]

    async def list_short_term(self) -> list[ShortTermDesire]:
        """全部短期欲望（含 satisfied/expired 历史），供 /api/desires
        全量快照；最新在前。"""
        async with self._db.lock:
            cursor = await self._db.conn.execute(
                f"SELECT {_STD_COLS} FROM short_term_desire ORDER BY created_at DESC"
            )
            rows = await cursor.fetchall()
        return [_row_to_std(r) for r in rows]

    async def update_desire(self, desire: ShortTermDesire) -> None:
        async with self._db.lock:
            await self._db.conn.execute(
                "UPDATE short_term_desire SET type = ?, strength = ?, description = ?, "
                "goal = ?, retry_count = ?, status = ? WHERE id = ?",
                (
                    desire.type.value, desire.strength, desire.description,
                    _goal_json(desire.goal), desire.retry_count, desire.status.value,
                    desire.id,
                ),
            )
            await self._db.conn.commit()

    # —— desire_value ——

    async def get_value(self, type_: DesireType) -> DesireValue | None:
        async with self._db.lock:
            cursor = await self._db.conn.execute(
                f"SELECT {_VALUE_COLS} FROM desire_value WHERE type = ?",
                (type_.value,),
            )
            row = await cursor.fetchone()
        return _row_to_value(row) if row is not None else None

    async def list_values(self) -> list[DesireValue]:
        async with self._db.lock:
            cursor = await self._db.conn.execute(
                f"SELECT {_VALUE_COLS} FROM desire_value"
            )
            rows = await cursor.fetchall()
        return [_row_to_value(r) for r in rows]

    async def upsert_value(self, dv: DesireValue) -> None:
        async with self._db.lock:
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
            await self._db.conn.commit()

    # —— long_term_desire ——

    async def insert_long_term(self, desire: LongTermDesire) -> None:
        async with self._db.lock:
            await self._db.conn.execute(
                f"INSERT INTO long_term_desire ({_LT_COLS}) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                _lt_row(desire),
            )
            await self._db.conn.commit()

    async def list_long_term(self) -> list[LongTermDesire]:
        async with self._db.lock:
            cursor = await self._db.conn.execute(
                f"SELECT {_LT_COLS} FROM long_term_desire"
            )
            rows = await cursor.fetchall()
        return [_row_to_lt(r) for r in rows]

    async def update_long_term(self, desire: LongTermDesire) -> None:
        async with self._db.lock:
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
            await self._db.conn.commit()


def _std_row(
    d: ShortTermDesire
) -> tuple[str, float, str, float, str, str | None, int, str]:
    return (
        d.id, d.created_at, d.type.value, d.strength, d.description,
        _goal_json(d.goal), d.retry_count, d.status.value,
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
) -> tuple[str, float, str, str, str, float, float, str, str]:
    return (
        d.id, d.created_at, d.type.value, d.name, d.description,
        d.strength, d.progress, json.dumps(d.subtopics), json.dumps(d.linked_values),
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
