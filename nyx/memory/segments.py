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
from nyx.policy import policy
from nyx.types import ConversationSegment

EmbedFn = Callable[[str], Awaitable[list[float]]]
_LOGGER = logging.getLogger(__name__)
_MAX_GAP_SECONDS = policy("memory", "segments._MAX_GAP_SECONDS", int)
_BREAK_MARKERS = policy("memory", "segments._BREAK_MARKERS", tuple[str, ...])
_TOKEN_RE = policy("memory", "segments._TOKEN_RE", re.Pattern[str])
_EXPLICIT_RECALL = policy("memory", "segments._EXPLICIT_RECALL", re.Pattern[str])


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
    if content.strip() in policy(
        "memory", "segments.copy.should_continue_words", set[str]
    ):
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
    promote_threshold: int = policy(
        "memory", "segments.parameters.append_turn_promote_threshold", int
    ),
) -> ConversationSegment:
    if not event_id:
        raise ValueError(policy("memory", "segments.copy.append_turn_text_3", str))
    archived = await store.archived_conversation_turn(event_id)
    if archived is not None:
        return archived
    now = time.time() if timestamp is None else timestamp
    speaker = (
        policy("memory", "segments.copy.append_turn_text", str)
        if event_id.endswith(":nyx")
        else policy("memory", "segments.copy.append_turn_text_2", str)
    )
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
        latest.summary = latest.raw_content[
            -policy(
                "memory",
                "segments.parameters.append_turn_latest_summary_chars_or_items",
                int,
            ) :
        ]
        latest.topics = sorted(set(latest.topics) | _terms(content))[
            : policy(
                "memory",
                "segments.parameters.append_turn_latest_topics_chars_or_items",
                int,
            )
        ]
        segment = latest
    else:
        summary = content[
            -policy(
                "memory", "segments.parameters.append_turn_summary_chars_or_items", int
            ) :
        ]
        segment = ConversationSegment(
            id=f"segment-{uuid.uuid4().hex}",
            previous_id=latest.id if latest is not None else None,
            started_at=now,
            ended_at=now,
            raw_event_ids=[event_id],
            raw_content=raw_line,
            summary=summary,
            topics=sorted(_terms(content))[
                : policy(
                    "memory",
                    "segments.parameters.append_turn_topics_chars_or_items",
                    int,
                )
            ],
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
                    policy("memory", "segments.copy.append_turn_text_5", str),
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
                raise RuntimeError(
                    policy("memory", "segments.copy.append_turn_text_4", str)
                )
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
    limit: int = policy("memory", "segments.parameters.search_segments_limit", int),
    embed: EmbedFn | None = None,
    *,
    include_archived: bool = False,
    retrieval: ConversationRetrieval | None = None,
    max_excerpt_chars: int = policy(
        "memory", "segments.parameters.search_segments_max_excerpt_chars", int
    ),
) -> list[ConversationSegment]:
    """Retrieve conversation evidence; explicit recollection can reach cold archives."""
    query_terms = _terms(query)
    limit = max(
        0,
        min(limit, policy("memory", "segments.parameters.search_segments_limit", int)),
    )
    if not query_terms or not limit:
        return []
    query_embedding = None
    if embed is not None:
        try:
            query_embedding = await embed(query)
        except Exception:
            _LOGGER.exception(
                policy("memory", "segments.copy.search_segments_text", str)
            )
    include_archived = include_archived or is_recollection_query(query)
    ordered_terms = list(dict.fromkeys(text_tokens(strip_recall_fillers(query))))[
        : policy(
            "memory",
            "segments.parameters.search_segments_ordered_terms_chars_or_items",
            int,
        )
    ]
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
        lexical_match = overlap >= min(2, len(query_terms)) and lexical >= policy(
            "memory", "segments.parameters.search_segments_lexical_match_factor", float
        )
        if not lexical_match and cosine < policy(
            "memory", "segments.parameters.search_segments_value_factor_4", float
        ):
            continue
        score = max(lexical if lexical_match else 0.0, cosine)
        score += (
            policy("memory", "segments.parameters.search_segments_value_factor", float)
            if segment.long_term
            else 0.0
        )
        score += (
            policy(
                "memory", "segments.parameters.search_segments_value_factor_2", float
            )
            * segment.freshness
        )
        score += min(
            policy(
                "memory", "segments.parameters.search_segments_value_factor_3", float
            ),
            segment.ended_at
            / policy(
                "memory", "segments.parameters.search_segments_value_factor_5", float
            ),
        )
        chunks = split_conversation(segment.id, segment.raw_content)
        chunks.sort(
            key=lambda chunk: -len(query_terms & _terms(chunk.content)),
        )
        excerpt, ranges = evidence_excerpt(
            segment.raw_content,
            chunks[
                : policy(
                    "memory",
                    "segments.parameters.search_segments_excerpt_ranges_chars_or_items",
                    int,
                )
            ],
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
