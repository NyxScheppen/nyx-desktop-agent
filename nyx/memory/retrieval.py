import asyncio
import math
from collections.abc import Awaitable, Callable, Iterable
from typing import cast

from nyx.memory.graph import MemoryGraph
from nyx.memory.store import MemoryStore
from nyx.types import Memory

EmbedFn = Callable[[str], Awaitable[list[float]]]

_VECTOR_TOP_K = 5


def cosine(a: list[float], b: list[float]) -> float:
    """余弦相似度；任一方零向量或维度不一致返回 0.0。纯函数。"""
    if len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


def rank_by_cosine(
    query_vec: list[float], candidates: list[Memory]
) -> list[tuple[float, Memory]]:
    """全表余弦打分 + s>0 过滤 + 降序（embedding 缺失跳过）。纯函数。"""
    scored: list[tuple[float, Memory]] = []
    for m in candidates:
        if m.embedding is None:
            continue
        s = cosine(query_vec, m.embedding)
        if s > 0.0:
            scored.append((s, m))
    scored.sort(key=lambda t: t[0], reverse=True)
    return scored


def build_embed(model_name: str) -> EmbedFn:
    """用本地 sentence-transformers 建 embed 函数。

    惰性 import 避免未启用向量层时加载重依赖。
    """
    from sentence_transformers import SentenceTransformer

    model = SentenceTransformer(model_name)
    # sentence-transformers 的 encode 返回类型含 Unknown（SingleInput 里
    # PIL/torchcodec 可选导入兜底 None），getattr + cast 收窄为明确的
    # Callable，避免 pyright 报 partially-unknown。
    encode = cast(Callable[[str], Iterable[float]], getattr(model, "encode"))

    def _encode_sync(text: str) -> list[float]:
        return [float(x) for x in encode(text)]

    async def embed(text: str) -> list[float]:
        return await asyncio.to_thread(_encode_sync, text)

    return embed


class MemoryRetrieval:
    """三层检索：keyword → vector → association，去重合并。

    keyword（store）→ vector（embedding 余弦）→ association（networkx 扩散）。
    """

    def __init__(self, store: MemoryStore, embed: EmbedFn | None = None) -> None:
        self._store = store
        self._embed = embed          # None = 向量层禁用

    async def search(self, query: str, limit: int = 20) -> list[Memory]:
        if not query.strip():
            return []
        keyword_hits = await self._store.search_keyword(query)
        all_memories = await self._store.list_memories()
        by_id = {m.id: m for m in all_memories}

        vector_hits = await self._vector_search(query, all_memories)

        seed_ids = [m.id for m in (*keyword_hits, *vector_hits)]
        association_hits: list[Memory] = []
        if seed_ids:
            edges = await self._store.list_edges()
            related_ids = MemoryGraph(edges).neighbors(seed_ids)
            association_hits = [by_id[mid] for mid in related_ids if mid in by_id]

        merged: list[Memory] = []
        seen: set[str] = set()
        for m in (*keyword_hits, *vector_hits, *association_hits):
            if m.id not in seen:
                seen.add(m.id)
                merged.append(m)
        return merged[:limit]

    async def _vector_search(
        self, query: str, candidates: list[Memory]
    ) -> list[Memory]:
        if self._embed is None:
            return []
        qv = await self._embed(query)
        return [m for _, m in rank_by_cosine(qv, candidates)[:_VECTOR_TOP_K]]
