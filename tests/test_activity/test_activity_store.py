from typing import Any

from nyx import db
from nyx.activity.store import ActivityStore
from nyx.db import Database
from nyx.enums import ActivityStatus, ActivityType
from nyx.types import Activity


def _activity(
    id: str,
    type_: ActivityType = ActivityType.READING,
    status: ActivityStatus = ActivityStatus.PENDING,
    progress: dict[str, Any] | None = None,
    started_at: float = 1000.0,
    ended_at: float | None = None,
) -> Activity:
    return Activity(
        id=id,
        type=type_,
        schedule_block_id="09:00",
        status=status,
        progress=progress if progress is not None else {"desire_id": None},
        started_at=started_at,
        ended_at=ended_at,
    )


async def _new_store() -> tuple[ActivityStore, Database]:
    database = await db.connect(":memory:")
    return ActivityStore(database), database


async def test_insert_get_roundtrip() -> None:
    store, database = await _new_store()
    try:
        a = _activity(
            "a1", progress={"desire_id": "d1", "goal": {"action": "read"}}
        )
        await store.insert(a)
        got = await store.get("a1")
        assert got is not None
        assert got.type is ActivityType.READING
        assert got.status is ActivityStatus.PENDING
        assert got.progress == {"desire_id": "d1", "goal": {"action": "read"}}
    finally:
        await database.conn.close()


async def test_get_missing_returns_none() -> None:
    store, database = await _new_store()
    try:
        assert await store.get("nope") is None
    finally:
        await database.conn.close()


async def test_get_current_only_running_paused() -> None:
    store, database = await _new_store()
    try:
        await store.insert(
            _activity("a1", status=ActivityStatus.COMPLETED, started_at=1000.0)
        )
        await store.insert(
            _activity("a2", status=ActivityStatus.PAUSED, started_at=2000.0)
        )
        await store.insert(
            _activity("a3", status=ActivityStatus.RUNNING, started_at=3000.0)
        )
        cur = await store.get_current()
        assert cur is not None
        assert cur.id == "a3"
    finally:
        await database.conn.close()


async def test_get_last_exploration_empty() -> None:
    store, database = await _new_store()
    try:
        await store.insert(_activity("a1", started_at=1000.0))
        assert await store.get_last_exploration() == 0.0
    finally:
        await database.conn.close()


async def test_get_last_exploration_max() -> None:
    store, database = await _new_store()
    try:
        await store.insert(
            _activity(
                "a1", type_=ActivityType.FREE_EXPLORATION, started_at=1000.0
            )
        )
        await store.insert(
            _activity(
                "a2", type_=ActivityType.FREE_EXPLORATION, started_at=5000.0
            )
        )
        assert await store.get_last_exploration() == 5000.0
    finally:
        await database.conn.close()


async def test_list_schedule_filters_and_orders() -> None:
    store, database = await _new_store()
    try:
        await store.insert(_activity("a1", started_at=3000.0))
        await store.insert(_activity("a2", started_at=1000.0))
        await store.insert(_activity("a3", started_at=500.0))
        rows = await store.list_schedule(1000.0)
        assert [r.id for r in rows] == ["a2", "a1"]
    finally:
        await database.conn.close()


async def test_update() -> None:
    store, database = await _new_store()
    try:
        await store.insert(_activity("a1"))
        a = await store.get("a1")
        assert a is not None
        a.status = ActivityStatus.COMPLETED
        a.progress = {"result": {"book": "x"}}
        a.ended_at = 9999.0
        await store.update(a)
        got = await store.get("a1")
        assert got is not None
        assert got.status is ActivityStatus.COMPLETED
        assert got.progress == {"result": {"book": "x"}}
        assert got.ended_at == 9999.0
    finally:
        await database.conn.close()
