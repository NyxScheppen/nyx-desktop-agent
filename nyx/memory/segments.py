"""对话段判定：宽松连接，明确断开。"""

import logging
import re
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import asdict, replace
from math import sqrt

from nyx.memory.conversation_chunks import (
    embedding_version,
    evidence_excerpt,
    split_conversation,
    text_tokens,
)
from nyx.memory.conversation_retrieval import ConversationRetrieval
from nyx.memory.facts import strip_recall_fillers
from nyx.memory.store import MemoryStore
from nyx.types import ConversationSegment

EmbedFn = Callable[[str], Awaitable[list[float]]]
_LOGGER = logging.getLogger(__name__)
_MAX_GAP_SECONDS = 4 * 60 * 60
_BREAK_MARKERS = ("换个话题", "另外", "对了", "说到别的", "不聊这个了")
_TOKEN_RE = re.compile(r"[A-Za-z0-9_]+|[\u4e00-\u9fff]{2,}")
_EXPLICIT_RECALL = re.compile(
    r"记得|回忆|以前|之前|上次|曾经|当时|历史|那次|那天|还记|约好|"
    r"说过|提过|告诉过|聊过|remember|recall",
    re.I,
)


def is_recollection_query(query: str) -> bool:
    """Explicit recollection remains eligible even under discussion routing."""
    return bool(_EXPLICIT_RECALL.search(query))


def _terms(text: str) -> set[str]:
    result: set[str] = set()
    for token in _TOKEN_RE.findall(strip_recall_fillers(text)):
        if token.isascii():
            result.add(token.casefold())
        else:
            result.update(token[i : i + 2] for i in range(len(token) - 1))
    return result


def should_continue(segment: ConversationSegment, content: str, now: float) -> bool:
    """宽松续接：时间和相似度都是弱信号，明确转题才强制断开。"""
    if content.strip() in {"嗯", "嗯嗯", "好", "好的", "对", "不是", "可以"}:
        return True
    if now - segment.ended_at > _MAX_GAP_SECONDS:
        return bool(_terms(content) & set(segment.topics))
    if any(marker in content for marker in _BREAK_MARKERS):
        return False
    return bool(_terms(content) & set(segment.topics)) or not segment.topics


async def append_turn(
    store: MemoryStore,
    *,
    event_id: str,
    content: str,
    timestamp: float | None = None,
    embed: EmbedFn | None = None,
    promote_threshold: int = 3,
) -> ConversationSegment:
    if not event_id:
        raise ValueError("轮次 ID 不能为空")
    archived = await store.archived_conversation_turn(event_id)
    if archived is not None:
        return archived
    now = time.time() if timestamp is None else timestamp
    speaker = "尼克斯" if event_id.endswith(":nyx") else "用户"
    raw_line = f"{speaker}：{content}"
    latest = await store.latest_conversation_segment()
    snapshot = asdict(latest) if latest is not None else None
    if (
        latest is not None
        and latest.active
        and (event_id.endswith(":nyx") or should_continue(latest, content, now))
    ):
        latest.evaluated_at = None
        latest.evaluation_reason = ""
        latest.evidence_event_ids = []
        latest.long_term = latest.recall_count >= promote_threshold
        latest.facts_extracted_at = None
        latest.freshness = 1.0
        latest.freshness_updated_at = max(latest.freshness_updated_at, now)
        latest.started_at = min(latest.started_at, now)
        latest.ended_at = max(latest.ended_at, now)
        latest.raw_event_ids.append(event_id)
        latest.raw_content += "\n" + raw_line
        latest.summary = latest.raw_content[-2000:]
        latest.topics = sorted(set(latest.topics) | _terms(content))[:32]
        segment = latest
    else:
        summary = content[-2000:]
        segment = ConversationSegment(
            id=f"segment-{uuid.uuid4().hex}",
            previous_id=latest.id if latest is not None else None,
            started_at=now,
            ended_at=now,
            raw_event_ids=[event_id],
            raw_content=raw_line,
            summary=summary,
            topics=sorted(_terms(content))[:32],
        )
    current_chunks = {
        chunk.id: chunk
        for chunk in await store.list_conversation_chunks(
            segment.id,
        )
    }
    chunks = split_conversation(segment.id, segment.raw_content)
    for index, chunk in enumerate(chunks):
        old = current_chunks.get(chunk.id)
        if (old is not None and old.embedding is not None
                and old.embedding_version == embedding_version(embed)):
            chunks[index] = old
        elif embed is not None:
            try:
                chunks[index] = replace(
                    chunk, embedding=await embed(chunk.content),
                    embedding_version=embedding_version(embed),
                )
            except Exception:
                _LOGGER.exception(
                    "对话分块向量失败，保留原文及全文索引 chunk_id=%s",
                    chunk.id,
                )
    # Legacy readers retain a bounded vector; new recall searches every chunk.
    segment.embedding = chunks[-1].embedding if chunks else None
    try:
        async with store.db.transaction():
            archived = await store.archived_conversation_turn(event_id)
            if archived is not None:
                return archived
            current = await store.latest_conversation_segment()
            current_snapshot = asdict(current) if current is not None else None
            # Recall counters may change during embedding; preserve those writes.
            compared = ("recall_count", "long_term")
            if {k: v for k, v in (snapshot or {}).items() if k not in compared} != {
                k: v for k, v in (current_snapshot or {}).items() if k not in compared
            }:
                raise RuntimeError("对话段准备快照已变化")
            if current is not None and segment.id == current.id:
                segment.recall_count = current.recall_count
                segment.long_term = current.recall_count >= promote_threshold
                await store.clear_segment_evidence(segment.id)
                await store.update_conversation_segment(segment, chunks=chunks)
            else:
                await store.add_conversation_segment(segment, chunks=chunks)
            await store.db.conn.execute(
                "INSERT INTO conversation_turn_archive (turn_id, segment_id) "
                "VALUES (?, ?)",
                (event_id, segment.id),
            )
    except Exception:
        archived = await store.archived_conversation_turn(event_id)
        if archived is not None:
            return archived
        raise
    return segment


async def search_segments(
    store: MemoryStore,
    query: str,
    limit: int = 20,
    embed: EmbedFn | None = None,
    *,
    include_archived: bool = False,
    retrieval: ConversationRetrieval | None = None,
    max_excerpt_chars: int = 1600,
) -> list[ConversationSegment]:
    """Retrieve conversation evidence; explicit recollection can reach cold archives."""
    query_terms = _terms(query)
    limit = max(0, min(limit, 20))
    if not query_terms or not limit:
        return []
    query_embedding = None
    if embed is not None:
        try:
            query_embedding = await embed(query)
        except Exception:
            _LOGGER.exception("对话段查询向量失败，保留关键词召回")
    include_archived = include_archived or is_recollection_query(query)
    ordered_terms = list(dict.fromkeys(text_tokens(strip_recall_fillers(query))))[:64]
    indexed = await (retrieval or ConversationRetrieval(store)).search(
        query,
        ordered_terms,
        query_embedding,
        limit=limit,
        include_archived=include_archived,
        max_excerpt_chars=max_excerpt_chars,
        embedding_model=embedding_version(embed),
    )
    # Existing databases keep their original retrieval until historical backfill.
    segments = await store.unindexed_conversation_segments(include_archived)
    ranked: list[tuple[float, ConversationSegment]] = []
    for segment in segments:
        terms = (
            set(segment.topics) | _terms(segment.summary) | _terms(segment.raw_content)
        )
        overlap = len(query_terms & terms)
        lexical = overlap / max(1, len(query_terms))
        cosine = 0.0
        # Untagged parent vectors cannot establish model compatibility. They
        # remain lexical-only until the versioned chunk backfill completes.
        if (not embedding_version(embed) and query_embedding is not None
                and segment.embedding is not None):
            if len(query_embedding) == len(segment.embedding):
                denominator = sqrt(sum(x * x for x in query_embedding)) * sqrt(
                    sum(x * x for x in segment.embedding)
                )
                if denominator:
                    cosine = (
                        sum(x * y for x, y in zip(query_embedding, segment.embedding))
                        / denominator
                    )
        lexical_match = overlap >= min(2, len(query_terms)) and lexical >= 0.25
        if not lexical_match and cosine < 0.45:
            continue
        score = max(lexical if lexical_match else 0.0, cosine)
        score += 0.2 if segment.long_term else 0.0
        score += 0.07 * segment.freshness
        score += min(0.1, segment.ended_at / 1e12)
        chunks = split_conversation(segment.id, segment.raw_content)
        chunks.sort(
            key=lambda chunk: -len(query_terms & _terms(chunk.content)),
        )
        excerpt, ranges = evidence_excerpt(
            segment.raw_content,
            chunks[:3],
            max_chars=max_excerpt_chars,
        )
        segment = replace(
            segment,
            retrieval_excerpt=excerpt or None,
            retrieval_ranges=ranges,
            retrieval_sources=["legacy"],
        )
        ranked.append((score, segment))
    ranked.sort(key=lambda item: (-item[0], -item[1].ended_at, item[1].id))
    seen = {segment.id for segment in indexed}
    return [*indexed, *(segment for _, segment in ranked if segment.id not in seen)][
        :limit
    ]
