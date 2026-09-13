import aiosqlite
import pytest

from nyx.db import connect
from nyx.enums import MemoryEdgeKind, MemoryType
from nyx.memory.store import MemoryStore, hash_content
from nyx.types import Memory, MemoryEdge


def _mem(
    id: str,
    *,
    created_at: float = 1.0,
    content: str = "content",
    tag: str = "general",
    summary: str = "summary",
    freshness: float = 0.5,
    type: MemoryType = MemoryType.SHORT_TERM,
    recall_count: int = 0,
    aspect: list[str] | None = None,
    embedding: list[float] | None = None,
) -> Memory:
    return Memory(
        id=id,
        created_at=created_at,
        content=content,
        tag=tag,
        summary=summary,
        freshness=freshness,
        type=type,
        recall_count=recall_count,
        aspect=aspect if aspect is not None else [],
        embedding=embedding,
    )


def test_memory_edge_kind_values() -> None:
    assert {k.value for k in MemoryEdgeKind} == {
        "semantic", "entity", "keyword", "temporal",
        "same_topic", "elaborates", "contrasts", "causes",
        "updates_preference", "user_profile_link",
    }


def test_memory_edge_defaults() -> None:
    edge = MemoryEdge("a", "b")
    assert edge.kind is MemoryEdgeKind.SEMANTIC
    assert edge.weight == 1.0
    assert edge.created_at == 0.0


async def test_add_get_roundtrip() -> None:
    db = await connect(":memory:")
    store = MemoryStore(db)
    try:
        mem = _mem(
            "m1",
            aspect=["身份背景", "情绪敏感点"],
            recall_count=3,
            embedding=[0.1, 0.2],
        )
        await store.add(mem)
        got = await store.get("m1")
        assert got == mem
    finally:
        await db.conn.close()


async def test_add_get_embedding_none() -> None:
    db = await connect(":memory:")
    store = MemoryStore(db)
    try:
        await store.add(_mem("m1", embedding=None))
        got = await store.get("m1")
        assert got is not None and got.embedding is None
    finally:
        await db.conn.close()


async def test_add_duplicate_id_raises() -> None:
    db = await connect(":memory:")
    store = MemoryStore(db)
    try:
        await store.add(_mem("m1"))
        with pytest.raises(aiosqlite.IntegrityError):
            await store.add(_mem("m1"))
    finally:
        await db.conn.close()


async def test_get_miss_returns_none() -> None:
    db = await connect(":memory:")
    store = MemoryStore(db)
    try:
        assert await store.get("ghost") is None
    finally:
        await db.conn.close()


async def test_list_memories_filters_and_sorts() -> None:
    db = await connect(":memory:")
    store = MemoryStore(db)
    try:
        await store.add(_mem("m1", tag="a", freshness=0.3))
        await store.add(_mem("m2", tag="b", type=MemoryType.LONG_TERM, freshness=0.9))
        await store.add(_mem("m3", tag="a", type=MemoryType.LONG_TERM, freshness=0.6))
        assert [m.id for m in await store.list_memories()] == ["m2", "m3", "m1"]
        assert [m.id for m in await store.list_memories(tag="a")] == ["m3", "m1"]
        by_type = await store.list_memories(type=MemoryType.LONG_TERM)
        assert [m.id for m in by_type] == ["m2", "m3"]
        combo = await store.list_memories(tag="a", type=MemoryType.LONG_TERM)
        assert [m.id for m in combo] == ["m3"]
    finally:
        await db.conn.close()


async def test_list_memories_limit() -> None:
    db = await connect(":memory:")
    store = MemoryStore(db)
    try:
        await store.add(_mem("m1", tag="a", freshness=0.3))
        await store.add(_mem("m2", tag="b", freshness=0.9))
        await store.add(_mem("m3", tag="a", freshness=0.6))
        assert [m.id for m in await store.list_memories(limit=2)] == ["m2", "m3"]
        assert [m.id for m in await store.list_memories(tag="a", limit=1)] == ["m3"]
    finally:
        await db.conn.close()


async def test_update_fields() -> None:
    db = await connect(":memory:")
    store = MemoryStore(db)
    try:
        await store.add(_mem("m1", created_at=111.0))
        await store.update_many(
            [
                _mem(
                    "m1",
                    created_at=999.0,
                    tag="new",
                    summary="s2",
                    freshness=0.8,
                    type=MemoryType.LONG_TERM,
                    recall_count=5,
                    aspect=["x"],
                    embedding=[0.9],
                )
            ]
        )
        got = await store.get("m1")
        assert got is not None
        assert got.id == "m1" and got.created_at == 111.0
        assert got.tag == "new" and got.summary == "s2"
        assert got.type is MemoryType.LONG_TERM and got.freshness == 0.8
        assert got.recall_count == 5 and got.aspect == ["x"]
        assert got.embedding == [0.9]
    finally:
        await db.conn.close()


async def test_update_many_keeps_content_hash_in_sync() -> None:
    db = await connect(":memory:")
    store = MemoryStore(db)
    try:
        await store.add(_mem("m1", content="old content"))
        await store.update_many([_mem("m1", content="new content")])

        assert await store.find_by_content("old content") is None
        found = await store.find_by_content("new content")
        assert found is not None and found.id == "m1"
    finally:
        await db.conn.close()


async def test_update_many() -> None:
    db = await connect(":memory:")
    store = MemoryStore(db)
    try:
        await store.add(_mem("m1", freshness=0.5, embedding=None))
        await store.add(_mem("m2", freshness=0.6))
        await store.update_many(
            [
                _mem("m1", freshness=0.1, tag="a", embedding=[0.5]),
                _mem("m2", freshness=0.2, summary="s2"),
            ]
        )
        m1 = await store.get("m1")
        m2 = await store.get("m2")
        assert m1 is not None and m1.freshness == 0.1 and m1.tag == "a"
        assert m1.embedding == [0.5]
        assert m2 is not None and m2.freshness == 0.2 and m2.summary == "s2"
        await store.update_many([])  # 空列表 no-op
    finally:
        await db.conn.close()


async def test_delete_cascades_edges() -> None:
    db = await connect(":memory:")
    store = MemoryStore(db)
    try:
        await store.add(_mem("a"))
        await store.add(_mem("b"))
        await store.add(_mem("c"))
        await store.upsert_edge("a", "b", MemoryEdgeKind.SEMANTIC, 1.0, 10.0)
        await store.upsert_edge("b", "a", MemoryEdgeKind.SEMANTIC, 2.0, 11.0)
        await store.upsert_edge("b", "c", MemoryEdgeKind.KEYWORD, 3.0, 12.0)
        await store.delete_many(["a"])
        assert await store.get("a") is None
        edges = [(e.from_id, e.to_id, e.kind) for e in await store.list_edges()]
        assert edges == [("b", "c", MemoryEdgeKind.KEYWORD)]
    finally:
        await db.conn.close()


async def test_delete_many() -> None:
    db = await connect(":memory:")
    store = MemoryStore(db)
    try:
        await store.add(_mem("a"))
        await store.add(_mem("b"))
        await store.add(_mem("c"))
        await store.upsert_edge("a", "b", MemoryEdgeKind.SEMANTIC, 1.0, 10.0)
        await store.upsert_edge("b", "c", MemoryEdgeKind.KEYWORD, 2.0, 11.0)
        await store.delete_many(["a", "b"])
        assert await store.get("a") is None
        assert await store.get("b") is None
        assert await store.get("c") is not None
        assert await store.list_edges() == []
        await store.delete_many([])  # 空列表 no-op
    finally:
        await db.conn.close()


async def test_record_recall_atomic() -> None:
    db = await connect(":memory:")
    store = MemoryStore(db)
    try:
        await store.add(_mem("m1"))
        # 未达阈值：加一 + 不升型，返回 False
        assert await store.record_recall("m1", promote_threshold=3) is False
        assert await store.record_recall("m1", promote_threshold=3) is False
        got = await store.get("m1")
        assert got is not None
        assert got.recall_count == 2 and got.type is MemoryType.SHORT_TERM
        # 达阈值：升长期，返回 True
        assert await store.record_recall("m1", promote_threshold=3) is True
        got = await store.get("m1")
        assert got is not None and got.type is MemoryType.LONG_TERM
        # 已长期：只递增、不再升型，返回 False
        assert await store.record_recall("m1", promote_threshold=3) is False
        got = await store.get("m1")
        assert got is not None and got.recall_count == 4
    finally:
        await db.conn.close()


async def test_search_keywords_returns_field_hits_ordered_and_capped() -> None:
    db = await connect(":memory:")
    store = MemoryStore(db)
    try:
        await store.add(
            _mem(
                "m1",
                content="alpha beta",
                summary="none",
                freshness=0.3,
                created_at=1.0,
            )
        )
        await store.add(
            _mem(
                "m2",
                content="alpha",
                summary="alpha beta",
                freshness=0.7,
                created_at=2.0,
            )
        )
        await store.add(
            _mem(
                "m3",
                content="beta",
                summary="none",
                freshness=1.0,
                created_at=3.0,
            )
        )
        hits = await store.search_keywords(["alpha", "beta"], limit=2)
        assert list(hits) == ["m2", "m1"]
        assert hits["m2"].summary_tokens == ["alpha", "beta"]
        assert hits["m2"].content_tokens == ["alpha"]
    finally:
        await db.conn.close()


async def test_search_keywords_empty_and_limit_zero_skip_db() -> None:
    db = await connect(":memory:")
    store = MemoryStore(db)
    try:
        assert await store.search_keywords([], limit=10) == {}
        assert await store.search_keywords(["alpha"], limit=0) == {}
    finally:
        await db.conn.close()


async def test_search_keywords_escapes_wildcards() -> None:
    db = await connect(":memory:")
    store = MemoryStore(db)
    try:
        await store.add(_mem("m1", content="进度 100%"))
        await store.add(_mem("m2", content="进度 100 元"))
        hits = await store.search_keywords(["100%"], limit=10)
        assert list(hits) == ["m1"]
    finally:
        await db.conn.close()


async def test_typed_edges_canonicalize_and_filter_kind() -> None:
    db = await connect(":memory:")
    store = MemoryStore(db)
    try:
        await store.add(_mem("a"))
        await store.add(_mem("b"))
        await store.upsert_edge("b", "a", MemoryEdgeKind.SEMANTIC, 0.4, 10.0)
        await store.upsert_edge("a", "b", MemoryEdgeKind.ENTITY, 0.8, 11.0)
        semantic = await store.list_edges(MemoryEdgeKind.SEMANTIC)
        all_edges = await store.list_edges()
        assert semantic == [MemoryEdge("a", "b", MemoryEdgeKind.SEMANTIC, 0.4, 10.0)]
        assert [e.kind for e in all_edges] == [
            MemoryEdgeKind.ENTITY,
            MemoryEdgeKind.SEMANTIC,
        ]
    finally:
        await db.conn.close()


async def test_delete_edges_and_list_edge_degrees() -> None:
    db = await connect(":memory:")
    store = MemoryStore(db)
    try:
        for mid in ("a", "b", "c"):
            await store.add(_mem(mid))
        await store.upsert_edge("a", "b", MemoryEdgeKind.SEMANTIC, 0.4, 10.0)
        await store.upsert_edge("b", "c", MemoryEdgeKind.KEYWORD, 0.5, 11.0)
        degrees = await store.list_edge_degrees(["b"])
        assert [(e.from_id, e.to_id, e.kind) for e in degrees["b"]] == [
            ("a", "b", MemoryEdgeKind.SEMANTIC),
            ("b", "c", MemoryEdgeKind.KEYWORD),
        ]
        await store.delete_edges([("a", "b", MemoryEdgeKind.SEMANTIC)])
        assert [(e.from_id, e.to_id, e.kind) for e in await store.list_edges()] == [
            ("b", "c", MemoryEdgeKind.KEYWORD)
        ]
    finally:
        await db.conn.close()


async def test_upsert_edge_unknown_id_raises() -> None:
    db = await connect(":memory:")
    store = MemoryStore(db)
    try:
        with pytest.raises(aiosqlite.IntegrityError):
            await store.upsert_edge(
                "ghost", "also_ghost", MemoryEdgeKind.SEMANTIC, 1.0, 10.0
            )
    finally:
        await db.conn.close()


def test_hash_content_deterministic() -> None:
    assert hash_content("同一句话") == hash_content("同一句话")
    assert hash_content("同一句话") != hash_content("另一句")
    assert len(hash_content("x")) == 64   # SHA-256 hex


async def test_find_by_content_hit_and_miss() -> None:
    db = await connect(":memory:")
    store = MemoryStore(db)
    try:
        await store.add(_mem("m1", content="同一句话"))
        found = await store.find_by_content("同一句话")
        assert found is not None and found.id == "m1"
        assert await store.find_by_content("别的内容") is None
    finally:
        await db.conn.close()


async def test_strengthen() -> None:
    db = await connect(":memory:")
    store = MemoryStore(db)
    try:
        await store.add(_mem("m1", recall_count=0, freshness=0.3))
        await store.strengthen("m1", 100.0)
        got = await store.get("m1")
        assert got is not None
        assert got.recall_count == 1      # 重复写入按设计计入 recall
        assert got.freshness == 1.0
        assert got.created_at == 1.0      # created_at 是创建时间，不随强化刷新
    finally:
        await db.conn.close()


async def test_count_new_ignores_strengthened_created_at() -> None:
    db = await connect(":memory:")
    store = MemoryStore(db)
    try:
        await store.add(_mem("m1", tag="reading", created_at=100.0))
        await store.strengthen("m1", 200.0)  # created_at / first_created_at 都不动
        assert await store.count_new("reading", 150.0) == 0  # 纯重读不算新增
        await store.add(_mem("m2", tag="reading", created_at=250.0))
        assert await store.count_new("reading", 150.0) == 1  # 真新增算 1
        assert await store.count_new(None, 150.0) == 1       # tag=None 全量计数
        assert await store.count_new("reading", 300.0) == 0  # since 更晚则都不算
        assert await store.count_new("user", 0.0) == 0       # 非目标 tag 不计
    finally:
        await db.conn.close()
