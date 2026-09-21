import re
import time
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime
from uuid import uuid4

import aiosqlite

from nyx.db import Database
from nyx.memory.retrieval import extract_keywords
from nyx.types import Memory, MemoryFact

_MAX_TEXT = 80
_FACT_TOKEN_RE = re.compile(r"[A-Za-z0-9_]+|[\u4e00-\u9fff]{2,}")
_MONTH_QUERY_RE = re.compile(
    r"(?:(20\d{2})年)?(1[0-2]|[1-9]|十一|十二|十|[一二三四五六七八九])月"
)
_CHINESE_MONTHS = {
    "一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6,
    "七": 7, "八": 8, "九": 9, "十": 10, "十一": 11, "十二": 12,
}


@dataclass
class FactCandidate:
    subject: str
    predicate: str
    object_value: str
    valid_from: float
    valid_until: float | None


def extract_fact_candidates(memory: Memory) -> list[FactCandidate]:
    """Extract a small deterministic fact set without making the write path fragile.

    The first implementation deliberately uses conservative lexical patterns.  It
    keeps fact updates available when an LLM is unavailable and avoids putting an
    external call inside the memory transaction.
    """
    text = f"{memory.summary}\n{memory.content}"
    result: list[FactCandidate] = []
    status_patterns = (
        (r"投简历|找工作|求职|正在.*找工作", "就业状态", "正在求职"),
        (r"已经在工作|已入职|已经入职|开始上班|在工作了", "就业状态", "已工作"),
    )
    for pattern, predicate, object_value in status_patterns:
        if re.search(pattern, text):
            result.append(
                FactCandidate(
                    "用户", predicate, object_value, memory.created_at, None
                )
            )
    preference = re.search(
        r"(?:我|用户).{0,8}(喜欢|不喜欢|讨厌)([^，。！？\n]{1,24})",
        text,
    )
    if preference is not None:
        result.append(
            FactCandidate(
                "用户",
                "偏好",
                f"{preference.group(1)}{preference.group(2).strip()}",
                memory.created_at,
                None,
            )
        )
    return result[:4]


def _fact_terms(text: str) -> set[str]:
    terms = set(extract_keywords(text))
    terms.update(match.group(0).lower() for match in _FACT_TOKEN_RE.finditer(text))
    return {term for term in terms if term}


def _query_time(text: str, fallback: float) -> float:
    """Use an explicit YYYY年M月/M月 query as the historical validity point."""
    match = _MONTH_QUERY_RE.search(text)
    if match is None:
        return fallback
    year = int(match.group(1) or datetime.fromtimestamp(fallback).year)
    raw_month = match.group(2)
    month = (
        int(raw_month)
        if raw_month.isdigit()
        else _CHINESE_MONTHS[raw_month]
    )
    if not 1 <= month <= 12:
        return fallback
    return datetime(year, month, 15).timestamp()


class MemoryFactStore:
    """实体与时间有效事实的 SQLite 存取层。"""

    def __init__(self, db: Database) -> None:
        self._db = db

    @property
    def db(self) -> Database:
        return self._db

    @asynccontextmanager
    async def _operation(self) -> AsyncGenerator[bool, None]:
        if self._db.in_transaction:
            yield False
            return
        async with self._db.lock:
            yield True

    async def apply(
        self, candidates: list[FactCandidate], source_memory_id: str | None
    ) -> None:
        if not candidates:
            return
        async with self._operation() as should_commit:
            for candidate in candidates:
                if (
                    candidate.valid_until is not None
                    and candidate.valid_until <= candidate.valid_from
                ):
                    continue
                subject_id = await self._ensure_entity(candidate.subject)
                object_id = await self._ensure_entity(candidate.object_value)
                duplicate = await self._find_duplicate(
                    subject_id, candidate.predicate, object_id,
                    candidate.object_value, candidate.valid_from,
                )
                if duplicate:
                    continue
                await self._close_replaced(
                    subject_id, candidate.predicate, candidate.valid_from
                )
                valid_until = await self._next_fact_start(
                    subject_id, candidate.predicate, candidate.valid_from,
                    candidate.valid_until,
                )
                await self._db.conn.execute(
                    "INSERT INTO memory_fact ("
                    "id, subject_entity_id, predicate, object_entity_id, object_value, "
                    "source_memory_id, valid_from, valid_until, created_at"
                    ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        str(uuid4()), subject_id, candidate.predicate, object_id,
                        candidate.object_value, source_memory_id,
                        candidate.valid_from, valid_until, time.time(),
                    ),
                )
            if should_commit:
                await self._db.conn.commit()

    async def search(self, query: str, now: float | None = None) -> list[MemoryFact]:
        if not query.strip():
            return []
        fallback = time.time() if now is None else now
        at = _query_time(query, fallback)
        async with self._operation():
            cursor = await self._db.conn.execute(
                "SELECT f.id, s.canonical_name AS subject, f.predicate, "
                "COALESCE(o.canonical_name, f.object_value) AS object_value, "
                "f.valid_from, f.valid_until, f.source_memory_id, f.created_at, "
                "f.subject_entity_id, f.object_entity_id "
                "FROM memory_fact f "
                "JOIN memory_entity s ON s.id = f.subject_entity_id "
                "LEFT JOIN memory_entity o ON o.id = f.object_entity_id "
                "WHERE f.valid_from <= ? AND "
                "(f.valid_until IS NULL OR f.valid_until > ?) "
                "ORDER BY f.valid_from DESC, f.created_at DESC, f.id",
                (at, at),
            )
            rows = await cursor.fetchall()
        terms = _fact_terms(query)
        direct_rows = [row for row in rows if self._matches(row, terms)]
        entity_ids: set[str] = set()
        for row in direct_rows:
            entity_ids.add(row["subject_entity_id"])
            object_id = row["object_entity_id"]
            if object_id is not None:
                entity_ids.add(object_id)
        selected = [
            row for row in rows
            if row["subject_entity_id"] in entity_ids
            or row["object_entity_id"] in entity_ids
        ]
        return [_row_to_fact(row) for row in selected]

    async def _ensure_entity(self, name: str) -> str:
        cursor = await self._db.conn.execute(
            "SELECT id FROM memory_entity WHERE canonical_name = ?", (name,)
        )
        row = await cursor.fetchone()
        if row is not None:
            return str(row["id"])
        entity_id = str(uuid4())
        await self._db.conn.execute(
            "INSERT INTO memory_entity "
            "(id, canonical_name, entity_type, aliases, created_at, updated_at) "
            "VALUES (?, ?, 'concept', '[]', ?, ?)",
            (entity_id, name, time.time(), time.time()),
        )
        return entity_id

    async def _find_duplicate(
        self,
        subject_id: str,
        predicate: str,
        object_id: str,
        object_value: str,
        valid_from: float,
    ) -> bool:
        cursor = await self._db.conn.execute(
            "SELECT 1 FROM memory_fact WHERE subject_entity_id = ? AND predicate = ? "
            "AND object_entity_id = ? AND object_value = ? AND valid_from = ? LIMIT 1",
            (subject_id, predicate, object_id, object_value, valid_from),
        )
        return await cursor.fetchone() is not None

    async def _close_replaced(
        self, subject_id: str, predicate: str, valid_from: float
    ) -> None:
        await self._db.conn.execute(
            "UPDATE memory_fact SET valid_until = ? "
            "WHERE subject_entity_id = ? AND predicate = ? "
            "AND valid_from < ? AND (valid_until IS NULL OR valid_until > ?)",
            (valid_from, subject_id, predicate, valid_from, valid_from),
        )

    async def _next_fact_start(
        self,
        subject_id: str,
        predicate: str,
        valid_from: float,
        valid_until: float | None,
    ) -> float | None:
        """Bound a late-arriving historical fact before the next known state."""
        cursor = await self._db.conn.execute(
            "SELECT MIN(valid_from) AS next_start FROM memory_fact "
            "WHERE subject_entity_id = ? AND predicate = ? AND valid_from > ?",
            (subject_id, predicate, valid_from),
        )
        row = await cursor.fetchone()
        next_start = float(row["next_start"]) if row and row["next_start"] else None
        if next_start is None:
            return valid_until
        if valid_until is None or next_start < valid_until:
            return next_start
        return valid_until

    @staticmethod
    def _matches(row: aiosqlite.Row, terms: set[str]) -> bool:
        if not terms:
            return False
        values = (
            str(row["subject"]).lower(),
            str(row["predicate"]).lower(),
            str(row["object_value"]).lower(),
        )
        return any(term in value or value in term for term in terms for value in values)


def _row_to_fact(row: aiosqlite.Row) -> MemoryFact:
    return MemoryFact(
        id=str(row["id"]),
        subject=str(row["subject"]),
        predicate=str(row["predicate"]),
        object_value=str(row["object_value"]),
        valid_from=float(row["valid_from"]),
        valid_until=(
            float(row["valid_until"]) if row["valid_until"] is not None else None
        ),
        source_memory_id=(
            str(row["source_memory_id"])
            if row["source_memory_id"] is not None else None
        ),
        created_at=float(row["created_at"]),
    )
