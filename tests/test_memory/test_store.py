import aiosqlite
import pytest

from nyx.db import connect
from nyx.enums import MemoryType
from nyx.memory.store import MemoryStore
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
        await store.upsert_edge("a", "b", 1.0)
        await store.upsert_edge("b", "a", 2.0)
        await store.upsert_edge("b", "c", 3.0)
        await store.delete_many(["a"])
        assert await store.get("a") is None
        edges = [(e.from_id, e.to_id) for e in await store.list_edges()]
        assert edges == [("b", "c")]
    finally:
        await db.conn.close()


async def test_delete_many() -> None:
    db = await connect(":memory:")
    store = MemoryStore(db)
    try:
        await store.add(_mem("a"))
        await store.add(_mem("b"))
        await store.add(_mem("c"))
        await store.upsert_edge("a", "b", 1.0)
        await store.upsert_edge("b", "c", 2.0)
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


async def test_search_keyword() -> None:
    db = await connect(":memory:")
    store = MemoryStore(db)
    try:
        await store.add(_mem("m1", content="deep sea", summary="no", freshness=0.3))
        await store.add(_mem("m2", content="x", summary="deep sea 读书", freshness=0.7))
        assert [m.id for m in await store.search_keyword("DEEP")] == ["m2", "m1"]
        assert await store.search_keyword("none") == []
    finally:
        await db.conn.close()


async def test_search_keyword_escapes_wildcards() -> None:
    db = await connect(":memory:")
    store = MemoryStore(db)
    try:
        await store.add(_mem("m1", content="进度 100%"))
        await store.add(_mem("m2", content="进度 100 元"))
        await store.add(_mem("m3", content="a_b"))
        await store.add(_mem("m4", content="aXb"))
        assert [m.id for m in await store.search_keyword("100%")] == ["m1"]
        assert [m.id for m in await store.search_keyword("a_b")] == ["m3"]
    finally:
        await db.conn.close()


async def test_list_edges_and_upsert() -> None:
    db = await connect(":memory:")
    store = MemoryStore(db)
    try:
        await store.add(_mem("a"))
        await store.add(_mem("b"))
        await store.upsert_edge("a", "b", 1.0)
        edges = await store.list_edges()
        assert edges == [MemoryEdge(from_id="a", to_id="b", weight=1.0)]
        await store.upsert_edge("a", "b", 2.5)
        edges = await store.list_edges()
        assert len(edges) == 1 and edges[0].weight == 2.5
    finally:
        await db.conn.close()


async def test_upsert_edge_unknown_id_raises() -> None:
    db = await connect(":memory:")
    store = MemoryStore(db)
    try:
        with pytest.raises(aiosqlite.IntegrityError):
            await store.upsert_edge("ghost", "also_ghost", 1.0)
    finally:
        await db.conn.close()
