"""对话段判定：宽松连接，明确断开。"""

import logging
import re
import time
import uuid
from collections.abc import Awaitable, Callable
from math import sqrt

from nyx.memory.facts import strip_recall_fillers
from nyx.memory.store import MemoryStore
from nyx.types import ConversationSegment

EmbedFn = Callable[[str], Awaitable[list[float]]]
_LOGGER = logging.getLogger(__name__)
_MAX_GAP_SECONDS = 4 * 60 * 60
_BREAK_MARKERS = ("换个话题", "另外", "对了", "说到别的", "不聊这个了")
_TOKEN_RE = re.compile(r"[A-Za-z0-9_]+|[\u4e00-\u9fff]{2,}")


def _terms(text: str) -> set[str]:
    result: set[str] = set()
    for token in _TOKEN_RE.findall(strip_recall_fillers(text)):
        if token.isascii():
            result.add(token)
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
    now = time.time() if timestamp is None else timestamp
    speaker = "尼克斯" if event_id.endswith(":nyx") else "用户"
    raw_line = f"{speaker}：{content}"
    latest = await store.latest_conversation_segment()
    if latest is not None and latest.active and (
        event_id.endswith(":nyx") or should_continue(latest, content, now)
    ):
        if latest.evaluated_at is not None:
            await store.clear_segment_evidence(latest.id)
        latest.evaluated_at = None
        latest.evaluation_reason = ""
        latest.evidence_event_ids = []
        latest.long_term = latest.recall_count >= promote_threshold
        latest.facts_extracted_at = None
        latest.freshness = 1.0
        latest.freshness_updated_at = now
        latest.ended_at = now
        latest.raw_event_ids.append(event_id)
        latest.raw_content += "\n" + raw_line
        latest.summary = latest.raw_content[-2000:]
        latest.topics = sorted(set(latest.topics) | _terms(content))[:32]
        if embed is not None:
            latest.embedding = await embed(latest.summary)
        await store.update_conversation_segment(latest)
        return latest
    summary = content[-2000:]
    embedding = await embed(summary) if embed is not None else None
    segment = ConversationSegment(
        id=f"segment-{uuid.uuid4().hex}",
        previous_id=latest.id if latest is not None else None,
        started_at=now,
        ended_at=now,
        raw_event_ids=[event_id],
        raw_content=raw_line,
        summary=summary,
        topics=sorted(_terms(content))[:32],
        embedding=embedding,
    )
    await store.add_conversation_segment(segment)
    return segment


async def search_segments(
    store: MemoryStore,
    query: str,
    limit: int = 20,
    embed: EmbedFn | None = None,
) -> list[ConversationSegment]:
    """按段摘要/原文主题召回，资料知识不在此候选集内。"""
    query_terms = _terms(query)
    if not query_terms:
        return []
    query_embedding = None
    if embed is not None:
        try:
            query_embedding = await embed(query)
        except Exception:
            _LOGGER.exception("对话段查询向量失败，保留关键词召回")
    segments = await store.list_conversation_segments(None, active_only=True)
    ranked: list[tuple[float, ConversationSegment]] = []
    for segment in segments:
        terms = set(segment.topics) | _terms(segment.summary)
        overlap = len(query_terms & terms)
        lexical = overlap / max(1, len(query_terms))
        cosine = 0.0
        if query_embedding is not None and segment.embedding is not None:
            if len(query_embedding) == len(segment.embedding):
                denominator = sqrt(sum(x * x for x in query_embedding)) * sqrt(
                    sum(x * x for x in segment.embedding)
                )
                if denominator:
                    cosine = sum(
                        x * y for x, y in zip(query_embedding, segment.embedding)
                    ) / denominator
        lexical_match = overlap >= min(2, len(query_terms)) and lexical >= 0.25
        if not lexical_match and cosine < 0.45:
            continue
        score = max(lexical if lexical_match else 0.0, cosine)
        score += 0.2 if segment.long_term else 0.0
        score += 0.07 * segment.freshness
        score += min(0.1, segment.ended_at / 1e12)
        ranked.append((score, segment))
    ranked.sort(key=lambda item: (-item[0], -item[1].ended_at, item[1].id))
    return [segment for _, segment in ranked[: max(0, min(limit, 20))]]
