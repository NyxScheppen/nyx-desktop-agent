import asyncio
import math
import re
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from typing import cast

from nyx.enums import MemoryType, SearchMode
from nyx.memory.ann import AnnIndex, ann_fingerprint
from nyx.memory.graph import MemoryGraph
from nyx.memory.store import KeywordSearchHit, MemoryStore
from nyx.types import Memory

EmbedFn = Callable[[str], Awaitable[list[float]]]

_RECALL_VECTOR_CANDIDATE_K = 80
_RECALL_KEYWORD_CANDIDATE_K = 80
_STOP_WORDS = {
    "这个",
    "那个",
    "什么",
    "怎么",
    "为什么",
    "然后",
    "就是",
    "一下",
    "可以",
    "还是",
    "一个",
    "我们",
    "你们",
}
_TOKEN_RE = re.compile(r"[A-Za-z0-9_]+|[\u4e00-\u9fff]+")


@dataclass
class RankedMemory:
    memory: Memory
    score: float
    vector_score: float
    keyword_score: float
    sources: list[SearchMode]


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
    """候选余弦打分 + s>0 过滤 + 降序（embedding 缺失跳过）。纯函数。"""
    scored: list[tuple[float, Memory]] = []
    for m in candidates:
        if m.embedding is None:
            continue
        s = cosine(query_vec, m.embedding)
        if s > 0.0:
            scored.append((s, m))
    scored.sort(key=lambda t: t[0], reverse=True)
    return scored


def extract_keywords(text: str) -> list[str]:
    """Extract stable LIKE tokens from mixed English/CJK text."""
    tokens: list[str] = []
    seen: set[str] = set()
    for match in _TOKEN_RE.finditer(text):
        raw = match.group(0)
        if raw.isascii():
            _append_token(raw.lower(), tokens, seen)
        elif 2 <= len(raw) <= 8:
            _append_token(raw, tokens, seen)
        else:
            for size in (2, 3):
                for index in range(0, len(raw) - size + 1):
                    _append_token(raw[index : index + size], tokens, seen)
    return tokens


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
    """Memory recall orchestration: fused direct recall, then graph association."""

    def __init__(self, store: MemoryStore, embed: EmbedFn | None = None) -> None:
        self._store = store
        self._embed = embed          # None = 向量层禁用
        self._ann: AnnIndex | None = None
        self._ann_fingerprint: tuple[tuple[str, float, int], ...] | None = None

    async def search(
        self,
        query: str,
        direct_limit: int = 20,
        association_limit: int = 10,
    ) -> list[Memory]:
        if not query.strip():
            return []
        if direct_limit <= 0:
            return []

        all_memories = await self._store.list_memories()
        by_id = {m.id: m for m in all_memories}
        query_vec = await self._embed_query(query)
        vector_scores = await self._vector_scores(query_vec, all_memories)
        tokens = extract_keywords(query)
        keyword_hits = await self._store.search_keywords(
            tokens, _RECALL_KEYWORD_CANDIDATE_K
        )
        direct_ranked = self._rank_direct(
            by_id, vector_scores, keyword_hits, len(tokens)
        )[:direct_limit]
        direct_memories = [ranked.memory for ranked in direct_ranked]
        for ranked in direct_ranked:
            ranked.memory.sources = ranked.sources
        if not direct_ranked or association_limit <= 0:
            return direct_memories

        seeds = {ranked.memory.id: ranked.score for ranked in direct_ranked}
        edges = await self._store.list_edges()
        association_hits = MemoryGraph(edges).associate(
            seeds,
            depth=2,
            limit=association_limit,
            exclude=set(seeds),
        )
        association_memories: list[Memory] = []
        for hit in association_hits:
            memory = by_id.get(hit.memory_id)
            if memory is None:
                continue
            memory.sources = [SearchMode.ASSOCIATION]
            association_memories.append(memory)
        return [*direct_memories, *association_memories]

    async def _ann_index(self, memories: list[Memory]) -> AnnIndex:
        fingerprint = ann_fingerprint(memories)
        if self._ann is None or fingerprint != self._ann_fingerprint:
            self._ann = AnnIndex.build(memories)
            self._ann_fingerprint = fingerprint
        return self._ann

    async def _embed_query(self, query: str) -> list[float] | None:
        if self._embed is None:
            return None
        try:
            return await self._embed(query)
        except Exception:
            return None

    async def _vector_scores(
        self, query_vec: list[float] | None, memories: list[Memory]
    ) -> dict[str, float]:
        if query_vec is None:
            return {}
        index = await self._ann_index(memories)
        scores: dict[str, float] = {}
        for candidate in index.query(query_vec, _RECALL_VECTOR_CANDIDATE_K):
            if candidate.cosine <= 0.0:
                continue
            scores[candidate.memory_id] = max(
                0.0, min(1.0, (candidate.cosine + 1.0) / 2.0)
            )
        return scores

    def _rank_direct(
        self,
        by_id: dict[str, Memory],
        vector_scores: dict[str, float],
        keyword_hits: dict[str, KeywordSearchHit],
        query_token_count: int,
    ) -> list[RankedMemory]:
        ranked: list[RankedMemory] = []
        candidate_ids = set(vector_scores) | set(keyword_hits)
        for memory_id in candidate_ids:
            memory = by_id.get(memory_id)
            if memory is None:
                continue
            vector_score = vector_scores.get(memory_id, 0.0)
            keyword_score = _keyword_score(
                keyword_hits.get(memory_id), query_token_count
            )
            type_boost = 1.0 if memory.type is MemoryType.LONG_TERM else 0.5
            score = (
                0.65 * vector_score
                + 0.25 * keyword_score
                + 0.07 * memory.freshness
                + 0.03 * type_boost
            )
            sources: list[SearchMode] = []
            if memory_id in vector_scores:
                sources.append(SearchMode.VECTOR)
            if memory_id in keyword_hits:
                sources.append(SearchMode.KEYWORD)
            ranked.append(
                RankedMemory(
                    memory=memory,
                    score=score,
                    vector_score=vector_score,
                    keyword_score=keyword_score,
                    sources=sources,
                )
            )
        ranked.sort(
            key=lambda item: (
                -item.score,
                -item.vector_score,
                -item.keyword_score,
                -item.memory.freshness,
                -item.memory.created_at,
                item.memory.id,
            )
        )
        return ranked


def _append_token(token: str, tokens: list[str], seen: set[str]) -> None:
    if len(token) <= 1 or token in _STOP_WORDS or token in seen:
        return
    seen.add(token)
    tokens.append(token)


def _keyword_score(hit: KeywordSearchHit | None, query_token_count: int) -> float:
    if hit is None or query_token_count <= 0:
        return 0.0
    summary_tokens = set(hit.summary_tokens)
    content_tokens = set(hit.content_tokens)
    matched_tokens = summary_tokens | content_tokens
    coverage = len(matched_tokens) / query_token_count
    summary_hit_ratio = len(summary_tokens) / query_token_count
    content_hit_ratio = len(content_tokens) / query_token_count
    field_score = min(1.0, 0.7 * summary_hit_ratio + 0.3 * content_hit_ratio)
    return min(1.0, 0.7 * coverage + 0.3 * field_score)
