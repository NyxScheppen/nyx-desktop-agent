"""Bounded BM25/vector recall, reciprocal-rank fusion, and source expansion."""

import asyncio
import logging
import time
from collections import defaultdict
from dataclasses import dataclass, field, replace
from uuid import uuid4

from nyx.enums import MemoryKind, MemoryType
from nyx.memory.ann import AnnIndex
from nyx.memory.conversation_chunks import (
    CHUNKER_VERSION,
    ConversationChunk,
    evidence_excerpt,
)
from nyx.memory.store import ConversationRetrievalHit, MemoryStore
from nyx.types import ConversationSegment, Memory

_LOGGER = logging.getLogger(__name__)
_CANDIDATES = 80


@dataclass
class _Indexes:
    chunks: dict[str, ConversationChunk] = field(
        default_factory=dict[str, ConversationChunk],
    )
    vectors: dict[tuple[str, int], AnnIndex] = field(
        default_factory=dict[tuple[str, int], AnnIndex],
    )
    version: int | None = None


class ConversationRetrieval:
    """Character-local retrieval with bounded candidate source reads."""

    def __init__(self, store: MemoryStore) -> None:
        self._store = store
        self._lock = asyncio.Lock()
        self._indexes: _Indexes | None = None

    async def _sync(self, *, transient: bool) -> _Indexes:
        indexes = None if transient else self._indexes
        try:
            version, changed, deleted = await self._store.conversation_chunk_changes(
                indexes.version if indexes is not None else None,
            )
            if indexes is None or "*" in deleted:
                indexes = _Indexes()
            for identity in [*(item for item in deleted if item != "*"),
                             *(chunk.id for chunk in changed)]:
                old = indexes.chunks.pop(identity, None)
                if old is not None and old.embedding is not None:
                    indexes.vectors[(old.embedding_version, len(old.embedding))].remove(
                        identity,
                    )
            for chunk in changed:
                indexes.chunks[chunk.id] = chunk
                if chunk.embedding is None:
                    continue
                index = indexes.vectors.setdefault(
                    (chunk.embedding_version, len(chunk.embedding)), AnnIndex(),
                )
                index.add(
                    Memory(
                        id=chunk.id,
                        created_at=float(chunk.start),
                        content=chunk.content,
                        kind=MemoryKind.INTERACTION,
                        summary="",
                        freshness=1.0,
                        type=MemoryType.SHORT_TERM,
                        embedding=chunk.embedding,
                    )
                )
            indexes.version = version
            if not transient:
                self._indexes = indexes
            return indexes
        except BaseException:
            if not transient:
                self._indexes = None
            raise

    async def search(
        self,
        query: str,
        terms: list[str],
        vector: list[float] | None,
        *,
        limit: int,
        include_archived: bool,
        max_excerpt_chars: int = 1600,
        embedding_model: str = "",
    ) -> list[ConversationSegment]:
        # A caller holding the DB transaction must not wait for an index reader
        # which may itself be waiting for the DB lock. Never publish dirty vectors.
        if self._store.db.in_transaction:
            return await self._search(
                await self._sync(transient=True),
                query,
                terms,
                vector,
                limit=limit,
                include_archived=include_archived,
                max_excerpt_chars=max_excerpt_chars,
                embedding_model=embedding_model,
            )
        async with self._lock:
            indexes = await self._sync(transient=False)
            return await self._search(
                indexes,
                query,
                terms,
                vector,
                limit=limit,
                include_archived=include_archived,
                max_excerpt_chars=max_excerpt_chars,
                embedding_model=embedding_model,
            )

    async def _search(
        self,
        indexes: _Indexes,
        query: str,
        terms: list[str],
        vector: list[float] | None,
        *,
        limit: int,
        include_archived: bool,
        max_excerpt_chars: int,
        embedding_model: str,
    ) -> list[ConversationSegment]:
        started = time.monotonic()
        eligible = await self._store.eligible_conversation_chunks(include_archived)
        lexical = await self._store.search_conversation_chunks(
            terms,
            include_archived=include_archived,
            limit=_CANDIDATES,
        )
        # BM25 ranks candidates; coverage is only an admission gate against noise.
        lexical = [
            (identity, score, matched)
            for identity, score, matched in lexical
            if matched >= min(2, len(terms)) and matched / len(terms) >= 0.25
        ]
        vector_hits = (
            indexes.vectors[(embedding_model, len(vector))].query(
                vector,
                _CANDIDATES,
                allowed_ids=eligible,
            )
            if vector is not None and (embedding_model, len(vector)) in indexes.vectors
            else []
        )
        semantic = {
            hit.memory_id: hit.cosine for hit in vector_hits if hit.cosine >= 0.45
        }
        coverage = {identity: matched / len(terms) for identity, _, matched in lexical}
        fused: dict[str, float] = defaultdict(float)
        sources: dict[str, list[str]] = defaultdict(list)
        for source, identities in (
            ("bm25", [identity for identity, _, _ in lexical]),
            ("vector", list(semantic)),
        ):
            for rank, identity in enumerate(identities, 1):
                fused[identity] += 1.0 / (60 + rank)
                sources[identity].append(source)
        ranked: list[tuple[float, ConversationChunk]] = []
        for identity, fusion in fused.items():
            chunk = indexes.chunks.get(identity)
            if chunk is None or identity not in eligible:
                continue
            lexical_coverage = coverage.get(identity, 0.0)
            exact = float(query.casefold().strip() in chunk.content.casefold())
            # RRF avoids comparing incompatible BM25/cosine scales. A second,
            # content-based pass favours coverage and exact evidence over metadata.
            relevance = (
                0.5 * fusion * 30.5
                + 0.3 * max(lexical_coverage, semantic.get(identity, 0.0))
                + 0.15 * lexical_coverage
                + 0.05 * exact
            )
            ranked.append((relevance, chunk))
        ranked.sort(key=lambda item: (-item[0], item[1].id))
        by_segment: dict[str, list[tuple[float, ConversationChunk]]] = defaultdict(list)
        for score, chunk in ranked:
            by_segment[chunk.segment_id].append((score, chunk))
        results: list[tuple[float, ConversationSegment]] = []
        for segment_id, hits in by_segment.items():
            segment = await self._store.get_conversation_segment(segment_id)
            if segment is None or (not include_archived and not segment.active):
                continue
            valid = [
                (score, chunk)
                for score, chunk in hits
                if segment.raw_content[chunk.start : chunk.end] == chunk.content
            ]
            if not valid:
                continue
            excerpt, ranges = evidence_excerpt(
                segment.raw_content,
                [chunk for _, chunk in valid[:3]],
                max_chars=max_excerpt_chars,
            )
            if not excerpt:
                continue
            used = [
                chunk
                for _, chunk in valid[:3]
                if any(a <= chunk.start and chunk.end <= b for a, b in ranges)
            ]
            segment = replace(
                segment,
                retrieval_excerpt=excerpt,
                retrieval_chunk_ids=[chunk.id for chunk in used],
                retrieval_ranges=ranges,
                retrieval_sources=sorted(
                    {source for chunk in used for source in sources[chunk.id]}
                ),
            )
            results.append((valid[0][0], segment))
        results.sort(
            key=lambda item: (
                -item[0],
                -item[1].freshness,
                -int(item[1].long_term),
                -item[1].ended_at,
                item[1].id,
            )
        )
        selected = [segment for _, segment in results[:limit]]
        trace_id = uuid4().hex
        selected_chunks = {identity: segment for segment in selected
                           for identity in segment.retrieval_chunk_ids}
        bm25 = {identity: score for identity, score, _ in lexical}
        try:
            await self._store.save_conversation_trace(
                trace_id, query, f"{CHUNKER_VERSION}:{embedding_model}",
                include_archived, (time.monotonic() - started) * 1000,
                [ConversationRetrievalHit(
                    chunk, bm25.get(chunk.id), semantic.get(chunk.id),
                    fused[chunk.id], score, chunk.id in selected_chunks,
                    selected_chunks[chunk.id].retrieval_ranges
                    if chunk.id in selected_chunks else [],
                ) for score, chunk in ranked],
            )
            selected = [replace(segment, retrieval_trace_id=trace_id)
                        for segment in selected]
        except Exception:
            _LOGGER.exception("Conversation retrieval trace failed")
        _LOGGER.debug(
            "Conversation recall bm25=%d vector=%d segments=%d archived=%s",
            len(lexical),
            len(semantic),
            len(results),
            include_archived,
        )
        return selected
