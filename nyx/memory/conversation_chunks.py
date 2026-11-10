"""Deterministic, overlapping source slices for conversation retrieval."""

import hashlib
import re
from collections import Counter
from dataclasses import dataclass

_TURN_START = re.compile(r"(?m)^(?:用户|尼克斯)：")
_TOKENS = re.compile(r"[A-Za-z0-9_]+|[\u4e00-\u9fff]+")
_CHUNK_CHARS = 320
_OVERLAP_CHARS = 64
_SENTENCES = re.compile(r".+?(?:[。！？!?；;]+|\n+|$)", re.S)
CHUNKER_VERSION = "conversation-v1"


@dataclass(frozen=True)
class ConversationChunk:
    id: str
    segment_id: str
    start: int
    end: int
    content: str
    embedding: list[float] | None = None
    embedding_version: str = ""


def embedding_version(embed: object) -> str:
    """Custom encoders may provide index_version; empty is legacy/test only."""
    return str(getattr(embed, "index_version", ""))


def index_version(embed: object) -> str:
    return f"{CHUNKER_VERSION}:{embedding_version(embed) if embed else 'lexical'}"


def text_tokens(text: str) -> list[str]:
    """English words and Chinese bigrams share a portable inverted index."""
    tokens: list[str] = []
    for token in _TOKENS.findall(_TURN_START.sub("", text).casefold()):
        if token.isascii():
            tokens.append(token)
        elif len(token) == 1:
            tokens.append(token)
        else:
            tokens.extend(token[i : i + 2] for i in range(len(token) - 1))
    return tokens


def term_frequencies(text: str) -> Counter[str]:
    return Counter(text_tokens(text))


def split_conversation(segment_id: str, raw: str) -> list[ConversationChunk]:
    """Keep turn boundaries, overlap long turns, and retain exact source offsets.

    Speaker-looking lines are split points, not proof of message authorship.
    IDs depend on the source slice so appending a turn preserves earlier chunks.
    """
    boundaries = sorted({0, len(raw), *(m.start() for m in _TURN_START.finditer(raw))})
    topic_breaks: set[int] = set()
    for left, right in zip(boundaries, boundaries[1:]):
        if right - left <= _CHUNK_CHARS:
            continue
        previous = ""
        for sentence in _SENTENCES.finditer(raw, left, right):
            current = sentence.group()
            if previous and (
                "\n\n" in previous
                or (
                    max(len(previous), len(current)) >= 24
                    and not set(text_tokens(previous)) & set(text_tokens(current))
                )
            ):
                topic_breaks.add(sentence.start())
            previous = current
    boundaries = sorted({*boundaries, *topic_breaks})
    chunks: list[ConversationChunk] = []
    for left, right in zip(boundaries, boundaries[1:]):
        start = left
        while start < right:
            end = min(start + _CHUNK_CHARS, right)
            if end < right:
                stops = [
                    raw.rfind(char, start + _CHUNK_CHARS // 2, end)
                    for char in "\n。！？；.!?;"
                ]
                stop = max(stops)
                if stop >= 0:
                    end = stop + 1
            content = raw[start:end]
            if content.strip():
                digest = hashlib.sha256(
                    f"{segment_id}\0{start}\0{end}\0{content}".encode()
                ).hexdigest()
                chunks.append(
                    ConversationChunk(
                        f"conversation-{digest}",
                        segment_id,
                        start,
                        end,
                        content,
                    )
                )
            if end == right:
                break
            start = end - _OVERLAP_CHARS
    return chunks


def evidence_excerpt(
    raw: str,
    hits: list[ConversationChunk],
    *,
    max_chars: int = 1600,
) -> tuple[str, list[tuple[int, int]]]:
    """Expand around ranked hits, merge overlaps and bound material before rendering."""
    ranges: list[tuple[int, int]] = []
    for hit in hits:
        if raw[hit.start : hit.end] != hit.content:
            continue  # A stale index must never become evidence for edited source.
        start = max(0, hit.start - 160)
        end = min(len(raw), hit.end + 320)
        # Prefer full nearby lines (including the neighbouring answer) when bounded.
        line_start = raw.rfind("\n", start, hit.start)
        if line_start >= 0:
            start = line_start + 1
        line_end = raw.find("\n", hit.end, end)
        if line_end >= 0:
            end = line_end
        proposed: list[tuple[int, int]] = []
        for a, b in sorted([*ranges, (start, end)]):
            if proposed and a <= proposed[-1][1]:
                proposed[-1] = (proposed[-1][0], max(b, proposed[-1][1]))
            else:
                proposed.append((a, b))
        if sum(b - a for a, b in proposed) + 7 * len(proposed) <= max_chars:
            ranges = proposed
    return "\n[…]\n".join(raw[a:b] for a, b in ranges), ranges
