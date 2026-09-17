from nyx.db import connect
from nyx.enums import MemoryEdgeKind, MemoryKind, MemoryType, SearchMode
from nyx.memory.retrieval import (
    EmbedFn,
    MemoryRetrieval,
    cosine,
    extract_keywords,
    rank_by_cosine,
)
from nyx.memory.store import MemoryStore
from nyx.types import Memory


def _mem(
    id: str,
    *,
    content: str = "content",
    summary: str = "summary",
    freshness: float = 0.5,
    created_at: float = 1.0,
    type: MemoryType = MemoryType.SHORT_TERM,
    embedding: list[float] | None = None,
    topics: list[str] | None = None,
) -> Memory:
    return Memory(
        id=id,
        created_at=created_at,
        content=content,
        kind=MemoryKind.EPISODE,
        summary=summary,
        freshness=freshness,
        type=type,
        topics=topics or [],
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


def test_extract_keywords_mixed_text() -> None:
    assert extract_keywords("这个 Alpha_1 中文长句") == [
        "alpha_1",
        "中文长句",
    ]


def test_extract_keywords_keeps_short_cjk_with_particles() -> None:
    assert extract_keywords("可以吗你们好") == ["可以吗你们好"]


def test_extract_keywords_long_cjk_windows_without_stopword_splitting() -> None:
    tokens = extract_keywords("诺斯艾兰骑士团诺斯艾兰")
    assert "诺斯艾" in tokens
    assert "诺斯艾兰" not in tokens
    assert tokens.count("诺斯艾") == 1


def test_extract_keywords_long_cjk_keeps_stopword_windows() -> None:
    tokens = extract_keywords("和中文长句测试可以吗")
    assert "和中" in tokens
    assert "可以" not in tokens
    assert "可以吗" in tokens


async def test_search_fuses_vector_keyword_and_limits_direct_then_association() -> None:
    db = await connect(":memory:")
    store = MemoryStore(db)
    try:
        await store.add(_mem(
            "direct-vector",
            content="香蕉",
            summary="无",
            freshness=0.8,
            embedding=[1.0, 0.0],
        ))
        await store.add(_mem(
            "direct-keyword",
            content="alpha beta",
            summary="alpha beta",
            freshness=1.0,
            embedding=[0.0, 1.0],
        ))
        await store.add(_mem(
            "assoc",
            content="联想",
            summary="联想",
            freshness=1.0,
            embedding=None,
        ))
        await store.upsert_edge(
            "direct-vector", "assoc", MemoryEdgeKind.SEMANTIC, 1.0, 1.0
        )
        retrieval = MemoryRetrieval(store, embed=_fake_embed([1.0, 0.0]))
        results = await retrieval.search("alpha", direct_limit=2, association_limit=1)
        assert [m.id for m in results] == ["direct-vector", "direct-keyword", "assoc"]
        assert results[0].sources == [SearchMode.VECTOR]
        assert results[1].sources == [SearchMode.VECTOR, SearchMode.KEYWORD]
        assert results[2].sources == [SearchMode.ASSOCIATION]
    finally:
        await db.conn.close()


async def test_search_direct_limit_zero_returns_empty() -> None:
    db = await connect(":memory:")
    store = MemoryStore(db)
    try:
        await store.add(_mem("m1", content="alpha", embedding=[1.0, 0.0]))
        retrieval = MemoryRetrieval(store, embed=_fake_embed([1.0, 0.0]))
        results = await retrieval.search("alpha", direct_limit=0, association_limit=10)
        assert results == []
    finally:
        await db.conn.close()


async def test_search_adds_topic_only_association() -> None:
    db = await connect(":memory:")
    store = MemoryStore(db)
    try:
        await store.add(_mem("direct", content="alpha", topics=["信任"]))
        await store.add(_mem("topic-only", content="无关键词", topics=["信任"]))
        retrieval = MemoryRetrieval(store, embed=None)
        results = await retrieval.search("alpha", direct_limit=1, association_limit=5)
        assert [memory.id for memory in results] == ["direct", "topic-only"]
        assert results[1].sources == [SearchMode.ASSOCIATION]
    finally:
        await db.conn.close()


async def test_search_sources_keyword_only() -> None:
    db = await connect(":memory:")
    store = MemoryStore(db)
    try:
        # embed=None：向量层禁用，仅 keyword 命中
        await store.add(_mem("A", content="alpha 相关"))
        retrieval = MemoryRetrieval(store, embed=None)
        results = await retrieval.search("alpha")
        assert [m.id for m in results] == ["A"]
        assert [m.sources for m in results] == [[SearchMode.KEYWORD]]
    finally:
        await db.conn.close()


async def test_search_sources_vector_only() -> None:
    db = await connect(":memory:")
    store = MemoryStore(db)
    try:
        # content 不含 query 词（keyword 不命中），仅 embedding 余弦命中
        await store.add(_mem("A", content="香蕉", embedding=[1.0, 0.0]))
        retrieval = MemoryRetrieval(store, embed=_fake_embed([1.0, 0.0]))
        results = await retrieval.search("alpha")
        assert [m.id for m in results] == ["A"]
        assert [m.sources for m in results] == [[SearchMode.VECTOR]]
    finally:
        await db.conn.close()


async def test_search_sources_zero_cosine_vector_candidate() -> None:
    db = await connect(":memory:")
    store = MemoryStore(db)
    try:
        await store.add(_mem("A", content="香蕉", embedding=[0.0, 1.0]))
        retrieval = MemoryRetrieval(store, embed=_fake_embed([1.0, 0.0]))
        results = await retrieval.search("alpha")
        assert [m.id for m in results] == ["A"]
        assert [m.sources for m in results] == [[SearchMode.VECTOR]]
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
