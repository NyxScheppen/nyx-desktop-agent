import json
import time

from nyx import db
from nyx.enums import MemoryKind, MemoryType
from nyx.memory.facts import (
    FactCandidate,
    MemoryFactStore,
    extract_fact_candidates,
    parse_fact_extraction,
)
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


async def test_fact_store_same_start_conflict_keeps_one_current_fact() -> None:
    database = await db.connect(":memory:")
    try:
        store = MemoryFactStore(database)
        await store.apply(
            [
                FactCandidate("用户", "就业状态", "正在求职", 200.0, None),
                FactCandidate("用户", "就业状态", "已工作", 200.0, None),
            ],
            None,
        )
        facts = await store.search("用户 工作", now=250.0)
        assert [fact.object_value for fact in facts] == ["已工作"]
    finally:
        await database.close()


def test_extract_fact_candidates_is_conservative() -> None:
    memory = Memory(
        id="m1",
        created_at=time.time(),
        content="用户最近在投简历，准备找工作。",
        kind=MemoryKind.EPISODE,
        summary="用户在求职",
        freshness=1.0,
        type=MemoryType.SHORT_TERM,
    )
    facts = extract_fact_candidates(memory)
    assert [(fact.predicate, fact.object_value) for fact in facts] == [
        ("就业状态", "正在求职")
    ]


def test_extract_fact_candidates_keeps_last_same_time_status() -> None:
    memory = Memory(
        id="m2",
        created_at=100.0,
        content="用户之前在找工作，后来已经入职。",
        kind=MemoryKind.EPISODE,
        summary="用户状态变化",
        freshness=1.0,
        type=MemoryType.SHORT_TERM,
    )
    facts = extract_fact_candidates(memory)
    assert [(fact.predicate, fact.object_value) for fact in facts] == [
        ("就业状态", "已工作")
    ]


def test_extract_fact_candidates_ignores_non_user_memory_kinds() -> None:
    memory = Memory(
        id="m3",
        created_at=100.0,
        content="书中人物正在找工作，也喜欢猫。",
        kind=MemoryKind.KNOWLEDGE,
        summary="小说情节",
        freshness=1.0,
        type=MemoryType.LONG_TERM,
    )
    assert extract_fact_candidates(memory) == []


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


async def test_fact_search_has_bounded_result_size() -> None:
    database = await db.connect(":memory:")
    try:
        store = MemoryFactStore(database)
        await store.apply(
            [
                FactCandidate("用户", f"属性{i}", f"值{i}", float(i), None)
                for i in range(70)
            ],
            None,
        )
        facts = await store.search("用户", now=100.0)
        assert len(facts) == 64
    finally:
        await database.close()


def test_parse_generic_facts_resolves_aliases_and_entity_types() -> None:
    parsed = parse_fact_extraction(
        json.dumps(
            {
                "entities": [
                    {"name": "尼克斯", "type": "agent", "aliases": ["Nyx"]},
                    {"name": "《百年孤独》", "type": "book", "aliases": []},
                ],
                "facts": [
                    {
                        "subject": "Nyx",
                        "predicate": "读过",
                        "object": "《百年孤独》",
                        "object_type": "book",
                        "mode": "multi",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        valid_from=100.0,
        source_scope="reading",
    )
    assert parsed.facts[0].subject == "尼克斯"
    assert parsed.facts[0].subject_type == "agent"
    assert parsed.facts[0].multi_valued is True


async def test_fact_store_keeps_multi_valued_relations_and_polarity() -> None:
    database = await db.connect(":memory:")
    try:
        store = MemoryFactStore(database)
        await store.apply(
            [
                FactCandidate(
                    "《书》",
                    "讨论主题",
                    "爱情",
                    100.0,
                    None,
                    object_type="concept",
                    multi_valued=True,
                ),
                FactCandidate(
                    "《书》",
                    "讨论主题",
                    "战争",
                    100.0,
                    None,
                    object_type="concept",
                    multi_valued=True,
                ),
            ],
            None,
        )
        facts = await store.search("书 主题", now=200.0)
        assert {fact.object_value for fact in facts} == {"爱情", "战争"}
        assert all(fact.polarity == 1 for fact in facts)
    finally:
        await database.close()


async def test_fact_store_allows_same_name_with_different_entity_types() -> None:
    database = await db.connect(":memory:")
    try:
        store = MemoryFactStore(database)
        await store.apply(
            [
                FactCandidate(
                    "同名",
                    "关联",
                    "对象",
                    100.0,
                    None,
                    subject_type="person",
                ),
                FactCandidate(
                    "同名",
                    "关联",
                    "对象",
                    100.0,
                    None,
                    subject_type="book",
                ),
            ],
            None,
        )
        cursor = await database.conn.execute(
            "SELECT COUNT(*) FROM memory_entity WHERE canonical_name = '同名'"
        )
        row = await cursor.fetchone()
        assert row is not None and row[0] == 2
    finally:
        await database.close()
