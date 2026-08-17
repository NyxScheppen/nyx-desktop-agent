# 测试需直接访问 _vector_search（spec 测试要点要求测私有方法）
# pyright: reportPrivateUsage=false
from nyx.db import connect
from nyx.enums import MemoryType
from nyx.memory.retrieval import EmbedFn, MemoryRetrieval, cosine, rank_by_cosine
from nyx.memory.store import MemoryStore
from nyx.types import Memory


def _mem(
    id: str,
    *,
    content: str = "content",
    embedding: list[float] | None = None,
) -> Memory:
    return Memory(
        id=id,
        created_at=1.0,
        content=content,
        tag="general",
        summary="summary",
        freshness=0.5,
        type=MemoryType.SHORT_TERM,
        recall_count=0,
        aspect=[],
        embedding=embedding,
    )


def _fake_embed(vec: list[float]) -> EmbedFn:
    async def embed(_text: str) -> list[float]:
        return vec

    return embed


def test_cosine() -> None:
    assert cosine([1.0, 0.0], [0.0, 1.0]) == 0.0   # 正交
    assert cosine([1.0, 0.0], [1.0, 0.0]) == 1.0   # 相同
    assert cosine([1.0, 0.0], [-1.0, 0.0]) == -1.0  # 相反
    assert cosine([0.0, 0.0], [1.0, 0.0]) == 0.0   # 零向量
    assert cosine([1.0, 0.0], [1.0, 0.0, 0.0]) == 0.0  # 维度不一致


def test_rank_by_cosine() -> None:
    qv = [1.0, 0.0]
    candidates = [
        _mem("m1", embedding=[1.0, 0.0]),    # cos=1
        _mem("m2", embedding=None),           # 跳过
        _mem("m3", embedding=[-1.0, 0.0]),   # cos=-1 过滤
        _mem("m4", embedding=[0.5, 0.5]),    # cos≈0.707
    ]
    ranked = rank_by_cosine(qv, candidates)
    assert [m.id for _, m in ranked] == ["m1", "m4"]


async def test_vector_search_skips_none_and_filters() -> None:
    db = await connect(":memory:")
    store = MemoryStore(db)
    try:
        retrieval = MemoryRetrieval(store, embed=_fake_embed([1.0, 0.0]))
        candidates = [
            _mem("m1", embedding=[1.0, 0.0]),   # cos=1 命中
            _mem("m2", embedding=None),          # 跳过
            _mem("m3", embedding=[-1.0, 0.0]),  # cos=-1 过滤
            _mem("m4", embedding=[0.0, 1.0]),   # cos=0 过滤
        ]
        hits = await retrieval._vector_search("q", candidates)
        assert [m.id for m in hits] == ["m1"]
    finally:
        await db.conn.close()


async def test_vector_search_top_k_truncates() -> None:
    db = await connect(":memory:")
    store = MemoryStore(db)
    try:
        retrieval = MemoryRetrieval(store, embed=_fake_embed([1.0, 0.0]))
        candidates = [
            _mem(f"m{i}", embedding=[1.0, i * 0.01]) for i in range(1, 8)
        ]
        hits = await retrieval._vector_search("q", candidates)
        assert len(hits) == 5
    finally:
        await db.conn.close()


async def test_vector_search_disabled_when_embed_none() -> None:
    db = await connect(":memory:")
    store = MemoryStore(db)
    try:
        retrieval = MemoryRetrieval(store, embed=None)
        hits = await retrieval._vector_search("q", [_mem("m1", embedding=[1.0, 0.0])])
        assert hits == []
    finally:
        await db.conn.close()


async def test_search_merge_order_and_limit() -> None:
    db = await connect(":memory:")
    store = MemoryStore(db)
    try:
        await store.add(_mem("A", content="关于 alpha 的记忆", embedding=[1.0, 0.0]))
        await store.add(_mem("B", content="关于 beta", embedding=[0.0, 1.0]))
        await store.add(_mem("C", content="关于 gamma", embedding=None))
        await store.upsert_edge("A", "B", 1.0)
        retrieval = MemoryRetrieval(store, embed=_fake_embed([1.0, 0.0]))
        assert [m.id for m in await retrieval.search("alpha")] == ["A", "B"]
        assert [m.id for m in await retrieval.search("alpha", limit=1)] == ["A"]
    finally:
        await db.conn.close()


async def test_search_dedup() -> None:
    db = await connect(":memory:")
    store = MemoryStore(db)
    try:
        await store.add(_mem("A", content="alpha 相关", embedding=[1.0, 0.0]))
        retrieval = MemoryRetrieval(store, embed=_fake_embed([1.0, 0.0]))
        assert [m.id for m in await retrieval.search("alpha")] == ["A"]
    finally:
        await db.conn.close()


async def test_search_empty() -> None:
    db = await connect(":memory:")
    store = MemoryStore(db)
    try:
        await store.add(_mem("A", content="无关内容"))
        retrieval = MemoryRetrieval(store, embed=None)
        assert await retrieval.search("zzz") == []
    finally:
        await db.conn.close()


async def test_search_blank_query_returns_empty() -> None:
    db = await connect(":memory:")
    store = MemoryStore(db)
    try:
        # 含空格，复现 LIKE '% %' 全量命中
        await store.add(_mem("A", content="有关 内容"))
        retrieval = MemoryRetrieval(store, embed=None)
        assert await retrieval.search("") == []
        assert await retrieval.search(" ") == []
        assert await retrieval.search("   ") == []
    finally:
        await db.conn.close()


async def test_search_no_edge_no_crash() -> None:
    db = await connect(":memory:")
    store = MemoryStore(db)
    try:
        await store.add(_mem("A", content="alpha 相关"))
        retrieval = MemoryRetrieval(store, embed=None)
        assert [m.id for m in await retrieval.search("alpha")] == ["A"]
    finally:
        await db.conn.close()
