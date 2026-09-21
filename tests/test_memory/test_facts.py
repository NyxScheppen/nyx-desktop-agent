import time

from nyx import db
from nyx.enums import MemoryKind, MemoryType
from nyx.memory.facts import FactCandidate, MemoryFactStore, extract_fact_candidates
from nyx.types import Memory


async def test_fact_store_replaces_old_valid_fact_and_keeps_history() -> None:
    database = await db.connect(":memory:")
    try:
        store = MemoryFactStore(database)
        await store.apply(
            [FactCandidate("用户", "就业状态", "正在求职", 100.0, None)],
            None,
        )
        await store.apply(
            [FactCandidate("用户", "就业状态", "已工作", 200.0, None)],
            None,
        )
        current = await store.search("用户 工作", now=250.0)
        historical = await store.search("用户 求职", now=150.0)
        assert [fact.object_value for fact in current] == ["已工作"]
        assert [fact.object_value for fact in historical] == ["正在求职"]
        assert historical[0].valid_until == 200.0
    finally:
        await database.close()


async def test_fact_store_duplicate_is_idempotent() -> None:
    database = await db.connect(":memory:")
    try:
        store = MemoryFactStore(database)
        candidate = FactCandidate("用户", "就业状态", "已工作", 200.0, None)
        await store.apply([candidate], None)
        await store.apply([candidate], None)
        cursor = await database.conn.execute("SELECT COUNT(*) FROM memory_fact")
        row = await cursor.fetchone()
        assert row is not None and row[0] == 1
    finally:
        await database.close()


def test_extract_fact_candidates_is_conservative() -> None:
    memory = Memory(
        id="m1",
        created_at=time.time(),
        content="我最近在投简历，准备找工作。",
        kind=MemoryKind.EPISODE,
        summary="用户在求职",
        freshness=1.0,
        type=MemoryType.SHORT_TERM,
    )
    facts = extract_fact_candidates(memory)
    assert [(fact.predicate, fact.object_value) for fact in facts] == [
        ("就业状态", "正在求职")
    ]


async def test_fact_search_only_returns_current_facts() -> None:
    database = await db.connect(":memory:")
    try:
        store = MemoryFactStore(database)
        await store.apply(
            [FactCandidate("用户", "就业状态", "已工作", 100.0, 200.0)],
            None,
        )
        assert await store.search("用户", now=99.0) == []
        assert [
            fact.object_value for fact in await store.search("用户", now=150.0)
        ] == ["已工作"]
        assert await store.search("用户", now=200.0) == []
    finally:
        await database.close()


async def test_late_historical_fact_stops_at_next_known_state() -> None:
    database = await db.connect(":memory:")
    try:
        store = MemoryFactStore(database)
        await store.apply(
            [FactCandidate("用户", "就业状态", "已工作", 200.0, None)], None
        )
        await store.apply(
            [FactCandidate("用户", "就业状态", "正在求职", 100.0, None)], None
        )
        cursor = await database.conn.execute(
            "SELECT valid_until FROM memory_fact WHERE object_value = '正在求职'"
        )
        row = await cursor.fetchone()
        assert row is not None and row[0] == 200.0
    finally:
        await database.close()


async def test_month_query_uses_historical_validity_point() -> None:
    database = await db.connect(":memory:")
    try:
        store = MemoryFactStore(database)
        await store.apply(
            [FactCandidate("用户", "就业状态", "正在求职", 1717200000.0, None)],
            None,
        )
        await store.apply(
            [FactCandidate("用户", "就业状态", "已工作", 1725926400.0, None)],
            None,
        )
        facts = await store.search("六月 用户", now=1730000000.0)
        assert [fact.object_value for fact in facts] == ["正在求职"]
    finally:
        await database.close()
