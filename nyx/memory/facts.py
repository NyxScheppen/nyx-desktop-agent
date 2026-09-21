import json
import re
import time
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import Any, cast
from uuid import uuid4

import aiosqlite

from nyx.db import Database
from nyx.enums import MemoryKind
from nyx.memory.retrieval import extract_keywords
from nyx.types import Memory, MemoryFact

_MAX_TEXT = 80
_FACT_RECALL_LIMIT = 64
_FACT_QUERY_HINT_RE = re.compile(
    r"工作|求职|投简历|入职|上班|喜欢|不喜欢|讨厌|偏好|状态|职业|公司|"
    r"生日|年龄|住|家里|六月|七月|八月|九月|十月|十一月|十二月|"
    r"[1-9一二三四五六七八九十十二]月|哪本书|书中|人物|作者|谁|关于|"
    r"讨论|讲了什么|主题|尼克斯|Nyx|古典文学|读过"
)
_FACT_TOKEN_RE = re.compile(r"[A-Za-z0-9_]+|[\u4e00-\u9fff]{2,}")
_MONTH_QUERY_RE = re.compile(
    r"(?:(20\d{2})年)?(1[0-2]|[1-9]|十一|十二|十|[一二三四五六七八九])月"
)
_CHINESE_MONTHS = {
    "一": 1,
    "二": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
    "十": 10,
    "十一": 11,
    "十二": 12,
}


@dataclass
class FactCandidate:
    subject: str
    predicate: str
    object_value: str
    valid_from: float
    valid_until: float | None
    subject_type: str = "person"
    object_type: str = "concept"
    aliases: tuple[str, ...] = ()
    polarity: int = 1
    multi_valued: bool = False


@dataclass
class FactExtraction:
    """Strict, bounded result returned by the generic fact extractor."""

    entities: list[dict[str, object]]
    facts: list[FactCandidate]


def is_fact_query(query: str) -> bool:
    return bool(query.strip()) and _FACT_QUERY_HINT_RE.search(query) is not None


_FACT_MEMORY_KINDS = {
    MemoryKind.EPISODE,
    MemoryKind.USER_PROFILE,
}

_FUNCTIONAL_PREDICATES = frozenset(
    {"就业状态", "当前职业", "居住地", "当前公司", "当前所在地", "婚姻状态"}
)


def parse_fact_extraction(
    raw: str,
    *,
    valid_from: float,
    source_scope: str,
) -> FactExtraction:
    """Parse bounded LLM JSON and reject ambiguous or unsafe candidates."""
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return FactExtraction([], [])
    if not isinstance(data, dict):
        return FactExtraction([], [])
    parsed_data = cast(dict[str, Any], data)
    raw_entities: object = parsed_data.get("entities", [])
    raw_facts: object = parsed_data.get("facts", [])
    entities: list[dict[str, object]] = []
    names: set[str] = set()
    entity_info: dict[str, tuple[str, tuple[str, ...]]] = {}
    alias_to_name: dict[str, str] = {}
    if isinstance(raw_entities, list):
        for raw_item in cast(list[Any], raw_entities)[:32]:
            if not isinstance(raw_item, dict):
                continue
            item = cast(dict[str, Any], raw_item)
            name = item.get("name")
            entity_type = item.get("type", "concept")
            if not isinstance(name, str) or not name.strip():
                continue
            if not isinstance(entity_type, str) or not entity_type.strip():
                entity_type = "concept"
            canonical = _normalize_entity_name(name)
            if not canonical or canonical in names:
                continue
            aliases_raw: object = item.get("aliases", [])
            alias_values = (
                cast(list[Any], aliases_raw) if isinstance(aliases_raw, list) else []
            )
            aliases = tuple(
                _normalize_entity_name(alias)
                for alias in alias_values[:8]
                if isinstance(alias, str) and _normalize_entity_name(alias)
            )
            names.add(canonical)
            entities.append(
                {
                    "name": canonical,
                    "type": entity_type.strip()[:32],
                    "aliases": aliases,
                }
            )
            entity_info[canonical] = (entity_type.strip()[:32], aliases)
            for alias in aliases:
                alias_to_name[alias] = canonical
    facts: list[FactCandidate] = []
    if isinstance(raw_facts, list):
        for raw_item in cast(list[Any], raw_facts)[:32]:
            if not isinstance(raw_item, dict):
                continue
            item = cast(dict[str, Any], raw_item)
            subject = item.get("subject")
            predicate = item.get("predicate")
            object_value = item.get("object")
            if (
                not isinstance(subject, str)
                or not isinstance(predicate, str)
                or not isinstance(object_value, str)
            ):
                continue
            subject_name = alias_to_name.get(
                _normalize_entity_name(subject), _normalize_entity_name(subject)
            )
            object_name = alias_to_name.get(
                _normalize_entity_name(object_value),
                _normalize_entity_name(object_value),
            )
            predicate_name = " ".join(predicate.strip().split())[:48]
            if not subject_name or not object_name or not predicate_name:
                continue
            # The source scope is the caller's explicit attribution guard.  The
            # model must resolve a subject; never invent one for an opaque scope.
            if subject_name in {"他", "她", "他们", "它", "这本书", "该书", "someone"}:
                continue
            polarity = -1 if item.get("polarity") in {-1, "negative", "否定"} else 1
            mode = item.get("mode")
            if mode == "functional":
                multi = False
            elif mode == "multi":
                multi = True
            else:
                multi = predicate_name not in _FUNCTIONAL_PREDICATES
            if source_scope == "quoted" and item.get("asserted") is not True:
                continue
            facts.append(
                FactCandidate(
                    subject_name,
                    predicate_name,
                    object_name,
                    valid_from,
                    _optional_float(item.get("valid_until")),
                    str(
                        item.get("subject_type")
                        or entity_info.get(subject_name, ("concept", ()))[0]
                    )[:32],
                    str(
                        item.get("object_type")
                        or entity_info.get(object_name, ("concept", ()))[0]
                    )[:32],
                    entity_info.get(subject_name, ("concept", ()))[1],
                    polarity,
                    multi,
                )
            )
    return FactExtraction(entities, facts[:32])


def _optional_float(value: object) -> float | None:
    return (
        float(value)
        if isinstance(value, (int, float)) and not isinstance(value, bool)
        else None
    )


def _normalize_entity_name(value: str) -> str:
    return " ".join(value.strip().split())[:120]


def extract_fact_candidates(
    memory: Memory, source_scope: str = "conversation"
) -> list[FactCandidate]:
    """Extract a small deterministic fact set without making the write path fragile.

    The first implementation deliberately uses conservative lexical patterns.  It
    keeps fact updates available when an LLM is unavailable and avoids putting an
    external call inside the memory transaction.
    """
    if memory.kind not in _FACT_MEMORY_KINDS:
        return []
    if source_scope == "user_observation" or (
        memory.kind is MemoryKind.USER_PROFILE and "window_title" in memory.aspect
    ):
        return []
    text = f"{memory.summary}\n{memory.content}"
    result_with_positions: list[tuple[int, FactCandidate]] = []
    status_patterns = (
        (r"投简历|找工作|求职|正在.*找工作", "就业状态", "正在求职"),
        (r"已经在工作|已入职|已经入职|开始上班|在工作了", "就业状态", "已工作"),
    )
    for pattern, predicate, object_value in status_patterns:
        for match in re.finditer(pattern, text):
            if not _has_user_anchor(text, match.start()):
                continue
            result_with_positions.append(
                (
                    match.start(),
                    FactCandidate(
                        "用户", predicate, object_value, memory.created_at, None
                    ),
                )
            )
    for preference in re.finditer(
        r"(不喜欢|讨厌|喜欢)([^，。！？\n]{1,24})",
        text,
    ):
        if not _has_user_anchor(text, preference.start()):
            continue
        result_with_positions.append(
            (
                preference.start(),
                FactCandidate(
                    "用户",
                    "偏好",
                    f"{preference.group(1)}{preference.group(2).strip()}",
                    memory.created_at,
                    None,
                    polarity=-1 if preference.group(1) in {"不喜欢", "讨厌"} else 1,
                ),
            )
        )
    result_with_positions.sort(key=lambda item: item[0])
    result: list[FactCandidate] = []
    positions: dict[tuple[str, str, float], int] = {}
    for _, candidate in result_with_positions:
        key = (candidate.subject, candidate.predicate, candidate.valid_from)
        existing_index = positions.get(key)
        if existing_index is None:
            positions[key] = len(result)
            result.append(candidate)
        else:
            result[existing_index] = candidate
    return result[:4]


def _has_user_anchor(text: str, position: int) -> bool:
    """Require a nearby first/second-person subject before recording a fact."""
    clause_start = (
        max(
            text.rfind("。", 0, position),
            text.rfind("！", 0, position),
            text.rfind("？", 0, position),
            text.rfind("\n", 0, position),
        )
        + 1
    )
    prefix = text[clause_start:position]
    return bool(re.search(r"(?:我|用户|我的|本人)", prefix))


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
    month = int(raw_month) if raw_month.isdigit() else _CHINESE_MONTHS[raw_month]
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
            savepoint = f"facts_{uuid4().hex}"
            if not should_commit:
                await self._db.conn.execute(f"SAVEPOINT {savepoint}")
            try:
                for candidate in candidates:
                    if (
                        candidate.valid_until is not None
                        and candidate.valid_until <= candidate.valid_from
                    ):
                        continue
                    subject_id = await self._ensure_entity(
                        candidate.subject, candidate.subject_type, candidate.aliases
                    )
                    object_id = await self._ensure_entity(
                        candidate.object_value, candidate.object_type
                    )
                    duplicate = await self._find_duplicate(
                        subject_id,
                        candidate.predicate,
                        object_id,
                        candidate.object_value,
                        candidate.valid_from,
                        candidate.polarity,
                    )
                    if duplicate:
                        continue
                    if not candidate.multi_valued:
                        await self._remove_same_start_conflicts(
                            subject_id,
                            candidate.predicate,
                            candidate.valid_from,
                            object_id,
                            candidate.polarity,
                        )
                        await self._close_replaced(
                            subject_id, candidate.predicate, candidate.valid_from
                        )
                    valid_until = candidate.valid_until
                    if not candidate.multi_valued:
                        valid_until = await self._next_fact_start(
                            subject_id,
                            candidate.predicate,
                            candidate.valid_from,
                            candidate.valid_until,
                        )
                    await self._db.conn.execute(
                        "INSERT INTO memory_fact ("
                        "id, subject_entity_id, predicate, object_entity_id, "
                        "object_value, source_memory_id, valid_from, valid_until, "
                        "created_at, polarity"
                        ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (
                            str(uuid4()),
                            subject_id,
                            candidate.predicate,
                            object_id,
                            candidate.object_value,
                            source_memory_id,
                            candidate.valid_from,
                            valid_until,
                            time.time(),
                            candidate.polarity,
                        ),
                    )
                if should_commit:
                    await self._db.conn.commit()
                else:
                    await self._db.conn.execute(f"RELEASE SAVEPOINT {savepoint}")
            except BaseException:
                if not should_commit:
                    await self._db.conn.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
                    await self._db.conn.execute(f"RELEASE SAVEPOINT {savepoint}")
                else:
                    await self._db.conn.rollback()
                raise

    async def search(self, query: str, now: float | None = None) -> list[MemoryFact]:
        if not query.strip():
            return []
        fallback = time.time() if now is None else now
        at = _query_time(query, fallback)
        terms = _fact_terms(query)
        if not terms:
            return []
        patterns = [f"%{_escape_like(term)}%" for term in terms]
        direct_where = " OR ".join(
            "s.canonical_name LIKE ? ESCAPE '\\' "
            "OR f.predicate LIKE ? ESCAPE '\\' "
            "OR COALESCE(o.canonical_name, f.object_value) LIKE ? ESCAPE '\\' "
            "OR s.aliases LIKE ? ESCAPE '\\' OR o.aliases LIKE ? ESCAPE '\\'"
            for _ in patterns
        )
        direct_params = [value for pattern in patterns for value in (pattern,) * 5]
        async with self._operation():
            cursor = await self._db.conn.execute(
                "SELECT f.id, s.canonical_name AS subject, f.predicate, "
                "COALESCE(o.canonical_name, f.object_value) AS object_value, "
                "f.valid_from, f.valid_until, f.source_memory_id, "
                "f.created_at, f.polarity, "
                "f.subject_entity_id, f.object_entity_id "
                "FROM memory_fact f "
                "JOIN memory_entity s ON s.id = f.subject_entity_id "
                "LEFT JOIN memory_entity o ON o.id = f.object_entity_id "
                "WHERE f.valid_from <= ? AND "
                "(f.valid_until IS NULL OR f.valid_until > ?) AND ("
                + direct_where
                + ") ORDER BY f.valid_from DESC, f.created_at DESC, f.id LIMIT ?",
                (at, at, *direct_params, _FACT_RECALL_LIMIT),
            )
            direct_rows = await cursor.fetchall()
            entity_ids: set[str] = set()
            for row in direct_rows:
                entity_ids.add(row["subject_entity_id"])
                object_id = row["object_entity_id"]
                if object_id is not None:
                    entity_ids.add(object_id)
            if not entity_ids:
                return []
            placeholders = ", ".join("?" for _ in entity_ids)
            ids = list(entity_ids)
            cursor = await self._db.conn.execute(
                "SELECT f.id, s.canonical_name AS subject, f.predicate, "
                "COALESCE(o.canonical_name, f.object_value) AS object_value, "
                "f.valid_from, f.valid_until, f.source_memory_id, "
                "f.created_at, f.polarity "
                "FROM memory_fact f "
                "JOIN memory_entity s ON s.id = f.subject_entity_id "
                "LEFT JOIN memory_entity o ON o.id = f.object_entity_id "
                "WHERE f.valid_from <= ? AND "
                "(f.valid_until IS NULL OR f.valid_until > ?) AND ("
                f"f.subject_entity_id IN ({placeholders}) OR "
                f"f.object_entity_id IN ({placeholders})) "
                "ORDER BY f.valid_from DESC, f.created_at DESC, f.id LIMIT ?",
                (at, at, *ids, *ids, _FACT_RECALL_LIMIT),
            )
            selected = await cursor.fetchall()
        return [_row_to_fact(row) for row in selected]

    async def recent(
        self, limit: int = 32, now: float | None = None
    ) -> list[MemoryFact]:
        """Return a bounded snapshot of currently valid facts for reflection."""
        at = time.time() if now is None else now
        bounded = max(0, min(limit, _FACT_RECALL_LIMIT))
        if bounded == 0:
            return []
        async with self._operation():
            cursor = await self._db.conn.execute(
                "SELECT f.id, s.canonical_name AS subject, f.predicate, "
                "COALESCE(o.canonical_name, f.object_value) AS object_value, "
                "f.valid_from, f.valid_until, f.source_memory_id, "
                "f.created_at, f.polarity "
                "FROM memory_fact f "
                "JOIN memory_entity s ON s.id = f.subject_entity_id "
                "LEFT JOIN memory_entity o ON o.id = f.object_entity_id "
                "WHERE f.valid_from <= ? AND "
                "(f.valid_until IS NULL OR f.valid_until > ?) "
                "ORDER BY f.created_at DESC, f.valid_from DESC, f.id LIMIT ?",
                (at, at, bounded),
            )
            rows = await cursor.fetchall()
        return [_row_to_fact(row) for row in rows]

    async def _ensure_entity(
        self, name: str, entity_type: str = "concept", aliases: tuple[str, ...] = ()
    ) -> str:
        normalized = _normalize_entity_name(name)
        type_name = entity_type.strip()[:32] or "concept"
        normalized_aliases = tuple(
            dict.fromkeys(
                alias
                for alias in (_normalize_entity_name(value) for value in aliases)
                if alias
            )
        )[:8]
        cursor = await self._db.conn.execute(
            "SELECT id, aliases FROM memory_entity "
            "WHERE canonical_name = ? AND entity_type = ?",
            (normalized, type_name),
        )
        row = await cursor.fetchone()
        if row is None:
            cursor = await self._db.conn.execute(
                "SELECT e.id, e.canonical_name, e.aliases "
                "FROM memory_entity_alias a "
                "JOIN memory_entity e ON e.id = a.entity_id "
                "WHERE a.alias = ? AND a.entity_type = ? LIMIT 1",
                (normalized, type_name),
            )
        if row is not None:
            current = json.loads(row["aliases"] or "[]")
            merged = list(dict.fromkeys([*current, *normalized_aliases]))[:8]
            if merged != current:
                await self._db.conn.execute(
                    "UPDATE memory_entity SET aliases = ?, updated_at = ? WHERE id = ?",
                    (json.dumps(merged, ensure_ascii=False), time.time(), row["id"]),
                )
            for alias in normalized_aliases:
                await self._db.conn.execute(
                    "INSERT OR IGNORE INTO memory_entity_alias "
                    "(entity_id, alias, entity_type) VALUES (?, ?, ?)",
                    (row["id"], alias, type_name),
                )
            return str(row["id"])
        entity_id = str(uuid4())
        await self._db.conn.execute(
            "INSERT INTO memory_entity "
            "(id, canonical_name, entity_type, aliases, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                entity_id,
                normalized,
                type_name,
                json.dumps(list(normalized_aliases), ensure_ascii=False),
                time.time(),
                time.time(),
            ),
        )
        for alias in normalized_aliases:
            await self._db.conn.execute(
                "INSERT OR IGNORE INTO memory_entity_alias "
                "(entity_id, alias, entity_type) VALUES (?, ?, ?)",
                (entity_id, alias, type_name),
            )
        return entity_id

    async def _find_duplicate(
        self,
        subject_id: str,
        predicate: str,
        object_id: str,
        object_value: str,
        valid_from: float,
        polarity: int,
    ) -> bool:
        cursor = await self._db.conn.execute(
            "SELECT 1 FROM memory_fact WHERE subject_entity_id = ? AND predicate = ? "
            "AND object_entity_id = ? AND object_value = ? AND valid_from = ? "
            "AND polarity = ? LIMIT 1",
            (subject_id, predicate, object_id, object_value, valid_from, polarity),
        )
        return await cursor.fetchone() is not None

    async def _remove_same_start_conflicts(
        self,
        subject_id: str,
        predicate: str,
        valid_from: float,
        object_id: str,
        polarity: int,
    ) -> None:
        await self._db.conn.execute(
            "DELETE FROM memory_fact WHERE subject_entity_id = ? "
            "AND predicate = ? AND valid_from = ? AND object_entity_id != ?",
            (subject_id, predicate, valid_from, object_id),
        )
        await self._db.conn.execute(
            "DELETE FROM memory_fact WHERE subject_entity_id = ? "
            "AND predicate = ? AND valid_from = ? AND object_entity_id = ? "
            "AND polarity != ?",
            (subject_id, predicate, valid_from, object_id, polarity),
        )

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
            if row["source_memory_id"] is not None
            else None
        ),
        created_at=float(row["created_at"]),
        polarity=int(row["polarity"]) if "polarity" in row.keys() else 1,
    )


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
