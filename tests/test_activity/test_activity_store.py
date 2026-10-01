from typing import Any

from nyx import db
from nyx.activity.store import ActivityStore
from nyx.db import Database
from nyx.enums import (
    ActivityStatus,
    ActivityType,
    AssignedTaskStatus,
    AssignedTaskType,
)
from nyx.types import Activity, AssignedTask


def _activity(
    id: str,
    type_: ActivityType = ActivityType.READING,
    status: ActivityStatus = ActivityStatus.PENDING,
    progress: dict[str, Any] | None = None,
    started_at: float = 1000.0,
    ended_at: float | None = None,
    schedule_block_id: str = "09:00",
) -> Activity:
    return Activity(
        id=id,
        type=type_,
        schedule_block_id=schedule_block_id,
        status=status,
        progress=progress if progress is not None else {"desire_id": None},
        started_at=started_at,
        ended_at=ended_at,
    )


async def _new_store() -> tuple[ActivityStore, Database]:
    database = await db.connect(":memory:")
    return ActivityStore(database), database


def _task(
    id: str,
    *,
    status: AssignedTaskStatus = AssignedTaskStatus.PENDING,
    url: str | None = "https://example.com/article",
    book_id: str | None = None,
    target: int | None = None,
    created_at: float = 1.0,
) -> AssignedTask:
    return AssignedTask(
        id=id,
        type=(AssignedTaskType.BOOK if book_id is not None else AssignedTaskType.WEB),
        status=status,
        url=url if book_id is None else None,
        book_id=book_id,
        target_paragraph=target,
        checkpoint={},
        error=None,
        created_at=created_at,
        updated_at=created_at,
    )


async def test_create_task_is_idempotent_while_active() -> None:
    store, database = await _new_store()
    try:
        first = await store.create_task(_task("t1"))
        duplicate = await store.create_task(_task("t2", created_at=2.0))
        tasks = await store.list_tasks()
    finally:
        await database.conn.close()
    assert duplicate.id == first.id
    assert [task.id for task in tasks] == ["t1"]


async def test_claim_task_and_activity_is_atomic_fifo() -> None:
    store, database = await _new_store()
    try:
        await store.create_task(_task("later", created_at=2.0))
        await store.create_task(
            _task("earlier", url="https://example.com/other", created_at=1.0)
        )
        pending = await store.get_next_task()
        assert pending is not None
        activity = _activity("a1", progress={"task_id": pending.id})
        async with database.transaction():
            claimed = await store.claim_task_for_activity(pending.id, 3.0)
            await store.insert(activity)
        saved = await store.get_task(pending.id)
    finally:
        await database.conn.close()
    assert pending.id == "earlier"
    assert claimed is True
    assert saved is not None and saved.status is AssignedTaskStatus.RUNNING


async def test_running_task_queries_and_status_update_share_transaction() -> None:
    store, database = await _new_store()
    try:
        await store.create_task(_task("t1"))
        activity = _activity("a1", progress={"task_id": "t1"})
        async with database.transaction():
            assert await store.claim_task_for_activity("t1", 2.0)
            await store.insert(activity)
            running = await store.list_running_tasks()
            linked = await store.get_latest_activity_for_task("t1")
            await store.set_task_status("t1", AssignedTaskStatus.PENDING, 3.0)
        saved = await store.get_task("t1")
    finally:
        await database.conn.close()
    assert [task.id for task in running] == ["t1"]
    assert linked is not None and linked.id == "a1"
    assert saved is not None
    assert saved.status is AssignedTaskStatus.PENDING


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


async def test_get_current_only_running() -> None:
    store, database = await _new_store()
    try:
        await store.insert(
            _activity("a1", status=ActivityStatus.COMPLETED, started_at=1000.0)
        )
        await store.insert(
            _activity("a2", status=ActivityStatus.ABANDONED, started_at=2000.0)
        )
        await store.insert(
            _activity("a3", status=ActivityStatus.RUNNING, started_at=3000.0)
        )
        cur = await store.get_current()
        assert cur is not None
        assert cur.id == "a3"
    finally:
        await database.conn.close()


async def test_list_running_all_started_at_asc() -> None:
    store, database = await _new_store()
    try:
        await store.insert(
            _activity("paused", status=ActivityStatus.PAUSED, started_at=2000.0)
        )
        await store.insert(
            _activity("late", status=ActivityStatus.RUNNING, started_at=3000.0)
        )
        await store.insert(
            _activity("early", status=ActivityStatus.RUNNING, started_at=1000.0)
        )
        rows = await store.list_running()
        assert [r.id for r in rows] == ["early", "late"]
    finally:
        await database.conn.close()


async def test_list_unfinished_includes_pending_and_running() -> None:
    store, database = await _new_store()
    try:
        await store.insert(
            _activity("done", status=ActivityStatus.COMPLETED, started_at=1.0)
        )
        await store.insert(
            _activity("running", status=ActivityStatus.RUNNING, started_at=3.0)
        )
        await store.insert(
            _activity("pending", status=ActivityStatus.PENDING, started_at=2.0)
        )

        rows = await store.list_unfinished()

        assert [row.id for row in rows] == ["pending", "running"]
    finally:
        await database.conn.close()


async def test_get_current_reuses_outer_transaction() -> None:
    store, database = await _new_store()
    try:
        await store.insert(_activity("a1", status=ActivityStatus.RUNNING))
        async with database.transaction():
            current = await store.get_current()
        assert current is not None
        assert current.id == "a1"
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


async def test_list_results_filters_and_orders() -> None:
    """list_results 只回「已完成 + 读书/探索/创作」，按 ended_at 倒序。"""
    store, database = await _new_store()
    try:
        await store.insert(
            _activity(
                "reading", type_=ActivityType.READING,
                status=ActivityStatus.COMPLETED, ended_at=1000.0,
            )
        )
        await store.insert(
            _activity(
                "creation", type_=ActivityType.CREATION,
                status=ActivityStatus.COMPLETED, ended_at=3000.0,
            )
        )
        await store.insert(
            _activity(
                "explore", type_=ActivityType.FREE_EXPLORATION,
                status=ActivityStatus.COMPLETED, ended_at=2000.0,
            )
        )
        # 非产出类型 / 非 completed 被过滤
        await store.insert(
            _activity(
                "observe", type_=ActivityType.OBSERVE_USER,
                status=ActivityStatus.COMPLETED, ended_at=4000.0,
            )
        )
        await store.insert(
            _activity(
                "running", type_=ActivityType.READING,
                status=ActivityStatus.RUNNING, ended_at=5000.0,
            )
        )
        rows = await store.list_results(100)
        assert [r.id for r in rows] == ["creation", "explore", "reading"]
    finally:
        await database.conn.close()


async def test_list_results_filters_creation_and_paginates() -> None:
    store, database = await _new_store()
    try:
        for index in range(4):
            await store.insert(
                _activity(
                    f"creation-{index}",
                    type_=ActivityType.CREATION,
                    status=ActivityStatus.COMPLETED,
                    ended_at=float(index),
                )
            )
        await store.insert(
            _activity(
                "reading",
                type_=ActivityType.READING,
                status=ActivityStatus.COMPLETED,
                ended_at=10.0,
            )
        )

        rows = await store.list_results(
            2, offset=1, activity_type=ActivityType.CREATION
        )

        assert [row.id for row in rows] == ["creation-2", "creation-1"]
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


async def test_get_paused_in_block_latest() -> None:
    """get_paused_in_block 只取当前块最新一条 PAUSED。"""
    store, database = await _new_store()
    try:
        await store.insert(
            _activity("a1", status=ActivityStatus.PAUSED, started_at=1000.0)
        )
        await store.insert(
            _activity("a2", status=ActivityStatus.PAUSED, started_at=2000.0)
        )
        await store.insert(
            _activity("other", status=ActivityStatus.PAUSED, schedule_block_id="10:00")
        )
        got = await store.get_paused_in_block("09:00", 0.0, 3000.0)
        assert got is not None
        assert got.id == "a2"     # 最新一条，且忽略其他块
    finally:
        await database.conn.close()


async def test_get_paused_in_block_none() -> None:
    """无当前块 PAUSED → None。"""
    store, database = await _new_store()
    try:
        await store.insert(
            _activity("a1", status=ActivityStatus.COMPLETED, started_at=1000.0)
        )
        assert await store.get_paused_in_block("09:00", 0.0, 3000.0) is None
    finally:
        await database.conn.close()


async def test_get_paused_in_block_excludes_previous_local_day() -> None:
    store, database = await _new_store()
    try:
        await store.insert(
            _activity(
                "yesterday",
                status=ActivityStatus.PAUSED,
                schedule_block_id="09:00",
                started_at=1000.0,
            )
        )
        await store.insert(
            _activity(
                "today",
                status=ActivityStatus.PAUSED,
                schedule_block_id="09:00",
                started_at=9000.0,
            )
        )
        got = await store.get_paused_in_block("09:00", 8000.0, 16000.0)
        assert got is not None
        assert got.id == "today"
    finally:
        await database.conn.close()
