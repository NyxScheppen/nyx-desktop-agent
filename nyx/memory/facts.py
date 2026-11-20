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
from nyx.policy import policy
from nyx.timezone import USER_TIMEZONE
from nyx.types import Memory, MemoryFact

_MAX_TEXT = policy("memory", "facts._MAX_TEXT", int)
_FACT_RECALL_LIMIT = policy("memory", "facts._FACT_RECALL_LIMIT", int)
_QUERY_FILLER_RE = policy("memory", "facts._QUERY_FILLER_RE", re.Pattern[str])
_PREDICATE_QUERIES = policy(
    "memory",
    "facts._PREDICATE_QUERIES",
    tuple[tuple[re.Pattern[str], tuple[str, ...]], ...],
)
_FACT_TOKEN_RE = policy("memory", "facts._FACT_TOKEN_RE", re.Pattern[str])
_MONTH_QUERY_RE = policy("memory", "facts._MONTH_QUERY_RE", re.Pattern[str])
_CHINESE_MONTHS = policy("memory", "facts._CHINESE_MONTHS", dict[str, int])


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
    return bool(_fact_terms(query))


def strip_recall_fillers(query: str) -> str:
    """Remove greetings and vocatives without inventing a retrieval topic."""
    return _QUERY_FILLER_RE.sub(" ", query.lower())


def format_source_time(timestamp: float | None) -> str:
    """Format an evidence clock; missing or invalid time stays explicitly unknown."""
    if timestamp is None or isinstance(timestamp, bool):
        return policy("memory", "facts.copy.format_source_time_text", str)
    try:
        return datetime.fromtimestamp(timestamp, USER_TIMEZONE).isoformat(
            timespec="seconds"
        )
    except (OverflowError, OSError, ValueError):
        return policy("memory", "facts.copy.format_source_time_text", str)


def is_knowledge_query(query: str) -> bool:
    """仅明确要求检索资料时开放知识事实，普通提及不算授权。"""
    source = re.search(
        policy("memory", "facts.copy.is_knowledge_query_text", str), query
    )
    request = re.search(
        policy("memory", "facts.copy.is_knowledge_query_text_2", str), query
    )
    return source is not None and request is not None


_FACT_MEMORY_KINDS = policy("memory", "facts._FACT_MEMORY_KINDS", set[MemoryKind])

_FUNCTIONAL_PREDICATES = policy(
    "memory", "facts._FUNCTIONAL_PREDICATES", frozenset[str]
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
        for raw_item in cast(list[Any], raw_entities)[
            : policy(
                "memory",
                "facts.parameters.parse_fact_extraction_value_chars_or_items",
                int,
            )
        ]:
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
                for alias in alias_values[
                    : policy(
                        "memory",
                        "facts.parameters.parse_fact_extraction_aliases_chars_or_items",
                        int,
                    )
                ]
                if isinstance(alias, str) and _normalize_entity_name(alias)
            )
            names.add(canonical)
            entities.append(
                {
                    "name": canonical,
                    "type": entity_type.strip()[
                        : policy(
                            "memory",
                            "facts.parameters.parse_fact_extraction_type_chars_or_items",
                            int,
                        )
                    ],
                    "aliases": aliases,
                }
            )
            entity_info[canonical] = (
                entity_type.strip()[
                    : policy(
                        "memory",
                        "facts.parameters.parse_fact_extraction_entity_info_canonical_chars_or_items",
                        int,
                    )
                ],
                aliases,
            )
            for alias in aliases:
                alias_to_name[alias] = canonical
    facts: list[FactCandidate] = []
    if isinstance(raw_facts, list):
        for raw_item in cast(list[Any], raw_facts)[
            : policy(
                "memory",
                "facts.parameters.parse_fact_extraction_value_chars_or_items_2",
                int,
            )
        ]:
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
            predicate_name = " ".join(predicate.strip().split())[
                : policy(
                    "memory",
                    "facts.parameters.parse_fact_extraction_predicate_name_chars_or_items",
                    int,
                )
            ]
            if not subject_name or not object_name or not predicate_name:
                continue
            # The source scope is the caller's explicit attribution guard.  The
            # model must resolve a subject; never invent one for an opaque scope.
            if subject_name in policy(
                "memory", "facts.copy.parse_fact_extraction_words", set[str]
            ):
                continue
            polarity = (
                -1
                if item.get("polarity")
                in {
                    -1,
                    "negative",
                    policy("memory", "facts.copy.parse_fact_extraction_text", str),
                }
                else 1
            )
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
                    )[
                        : policy(
                            "memory",
                            "facts.parameters.parse_fact_extraction_value_chars_or_items_3",
                            int,
                        )
                    ],
                    str(
                        item.get("object_type")
                        or entity_info.get(object_name, ("concept", ()))[0]
                    )[
                        : policy(
                            "memory",
                            "facts.parameters.parse_fact_extraction_value_chars_or_items_4",
                            int,
                        )
                    ],
                    entity_info.get(subject_name, ("concept", ()))[1],
                    polarity,
                    multi,
                )
            )
    return FactExtraction(
        entities,
        facts[
            : policy(
                "memory",
                "facts.parameters.parse_fact_extraction_result_chars_or_items",
                int,
            )
        ],
    )


def _optional_float(value: object) -> float | None:
    return (
        float(value)
        if isinstance(value, (int, float)) and not isinstance(value, bool)
        else None
    )


def _normalize_entity_name(value: str) -> str:
    return " ".join(value.strip().split())[
        : policy(
            "memory",
            "facts.parameters.normalize_entity_name_result_chars_or_items",
            int,
        )
    ]


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
        policy("memory", "facts.copy.extract_fact_candidates_words", tuple[str, ...]),
        policy("memory", "facts.copy.extract_fact_candidates_words_2", tuple[str, ...]),
    )
    for pattern, predicate, object_value in status_patterns:
        for match in re.finditer(pattern, text):
            if not _has_user_anchor(text, match.start()):
                continue
            result_with_positions.append(
                (
                    match.start(),
                    FactCandidate(
                        policy("memory", "facts.copy.has_user_anchor_text", str),
                        predicate,
                        object_value,
                        memory.created_at,
                        None,
                    ),
                )
            )
    for preference in re.finditer(
        policy("memory", "facts.copy.extract_fact_candidates_text", str),
        text,
    ):
        if not _has_user_anchor(text, preference.start()):
            continue
        result_with_positions.append(
            (
                preference.start(),
                FactCandidate(
                    policy("memory", "facts.copy.has_user_anchor_text", str),
                    policy("memory", "facts.copy.extract_fact_candidates_text_2", str),
                    f"{preference.group(1)}{preference.group(2).strip()}",
                    memory.created_at,
                    None,
                    polarity=-1
                    if preference.group(1)
                    in policy(
                        "memory", "facts.copy.extract_fact_candidates_words_3", set[str]
                    )
                    else 1,
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
    return result[
        : policy(
            "memory",
            "facts.parameters.extract_fact_candidates_result_chars_or_items",
            int,
        )
    ]


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
    return policy("memory", "facts.copy.has_user_anchor_text", str) in prefix


def _fact_terms(text: str) -> set[str]:
    cleaned = strip_recall_fillers(_MONTH_QUERY_RE.sub(" ", text))
    terms: set[str] = set()
    for token in _FACT_TOKEN_RE.findall(cleaned):
        if token.isascii():
            terms.add(token)
        else:
            terms.update(
                token[i : i + size]
                for size in policy("memory", "retrieval.ngram_sizes", tuple[int, ...])
                for i in range(len(token) - size + 1)
            )
    for pattern, predicates in _PREDICATE_QUERIES:
        if pattern.search(text):
            terms.update(predicates)
    if len(terms) > 1:
        terms.discard(policy("memory", "facts.copy.has_user_anchor_text", str))
    return terms


def _query_time(text: str, fallback: float) -> float:
    """Use an explicit local date, or the existing month midpoint convention."""
    date = re.search(policy("memory", "facts.copy.query_time_text", str), text)
    if date:
        try:
            return datetime(
                *(int(value) for value in date.groups()), tzinfo=USER_TIMEZONE
            ).timestamp()
        except (ValueError, OverflowError, OSError):
            return fallback
    match = _MONTH_QUERY_RE.search(text)
    if match is None:
        return fallback
    year = int(match.group(1) or datetime.fromtimestamp(fallback, USER_TIMEZONE).year)
    raw_month = match.group(2)
    month = int(raw_month) if raw_month.isdigit() else _CHINESE_MONTHS[raw_month]
    if not 1 <= month <= 12:
        return fallback
    try:
        return datetime(
            year,
            month,
            policy("memory", "facts.parameters.month_fallback_day", int),
            tzinfo=USER_TIMEZONE,
        ).timestamp()
    except (ValueError, OverflowError, OSError):
        return fallback


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
        self,
        candidates: list[FactCandidate],
        source_memory_id: str | None,
        scope: str = "conversation",
        source_segment_id: str | None = None,
        source_chunk_id: str | None = None,
    ) -> int:
        if not candidates:
            return 0
        created = 0
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
                        scope,
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
                            scope,
                        )
                        await self._close_replaced(
                            subject_id, candidate.predicate, candidate.valid_from, scope
                        )
                    valid_until = candidate.valid_until
                    if not candidate.multi_valued:
                        valid_until = await self._next_fact_start(
                            subject_id,
                            candidate.predicate,
                            candidate.valid_from,
                            candidate.valid_until,
                            scope,
                        )
                    await self._db.conn.execute(
                        "INSERT INTO memory_fact ("
                        "id, subject_entity_id, predicate, object_entity_id, "
                        "object_value, source_memory_id, valid_from, valid_until, "
                        "created_at, polarity, scope, source_segment_id, "
                        "source_chunk_id"
                        ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
                            (
                                scope
                                if scope in {"profile", "conversation", "knowledge"}
                                else "conversation"
                            ),
                            source_segment_id,
                            source_chunk_id,
                        ),
                    )
                    created += 1
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

        return created

    async def close_segment_facts(self, segment_id: str, now: float) -> None:
        async with self._operation() as should_commit:
            await self._db.conn.execute(
                "UPDATE memory_fact SET valid_until=? WHERE source_segment_id=? "
                "AND valid_from<? AND (valid_until IS NULL OR valid_until>?)",
                (now, segment_id, now, now),
            )
            if should_commit:
                await self._db.conn.commit()

    async def search(
        self, query: str, now: float | None = None, scope: str | None = None
    ) -> list[MemoryFact]:
        if not query.strip():
            return []
        fallback = time.time() if now is None else now
        at = _query_time(query, fallback)
        terms = _fact_terms(query)
        if not terms:
            return []
        patterns = [
            f"%{_escape_like(term)}%"
            for term in sorted(terms, key=lambda term: (-len(term), term))[
                : policy(
                    "memory", "facts.parameters.search_patterns_chars_or_items", int
                )
            ]
        ]
        knowledge_query = is_knowledge_query(query)
        allowed_scopes = (
            (scope,)
            if scope is not None
            else (("knowledge",) if knowledge_query else ("profile", "conversation"))
        )
        scope_placeholders = ", ".join("?" for _ in allowed_scopes)
        conditions = [
            "s.canonical_name LIKE ? ESCAPE '\\' "
            "OR f.predicate LIKE ? ESCAPE '\\' "
            "OR COALESCE(o.canonical_name, f.object_value) LIKE ? ESCAPE '\\' "
            "OR EXISTS (SELECT 1 FROM memory_entity_alias sa "
            "WHERE sa.entity_id = s.id AND sa.alias LIKE ? ESCAPE '\\') "
            "OR EXISTS (SELECT 1 FROM memory_entity_alias oa "
            "WHERE oa.entity_id = o.id AND oa.alias LIKE ? ESCAPE '\\')"
            for _ in patterns
        ]
        direct_where = " OR ".join(conditions)
        score = " + ".join(
            f"CASE WHEN ({condition}) THEN 1 ELSE 0 END" for condition in conditions
        )
        direct_params = [value for pattern in patterns for value in (pattern,) * 5]
        subject_where = ""
        subject_params: list[str] = []
        if (
            scope != "knowledge"
            and not knowledge_query
            and re.search(policy("memory", "facts.copy.search_text", str), query)
        ):
            subject_where = " AND s.canonical_name = ?"
            subject_params = policy("memory", "facts.copy.search_words", list[str])
        async with self._operation():
            cursor = await self._db.conn.execute(
                "SELECT f.id, s.canonical_name AS subject, f.predicate, "
                "COALESCE(o.canonical_name, f.object_value) AS object_value, "
                "f.valid_from, f.valid_until, f.source_memory_id, "
                "f.source_segment_id, f.source_chunk_id, "
                "f.created_at, f.polarity, f.scope, s.entity_type AS subject_type, "
                "o.entity_type AS object_type, "
                "f.subject_entity_id, f.object_entity_id "
                "FROM memory_fact f "
                "JOIN memory_entity s ON s.id = f.subject_entity_id "
                "LEFT JOIN memory_entity o ON o.id = f.object_entity_id "
                "WHERE f.scope IN (" + scope_placeholders + ") "
                "AND f.valid_from <= ? AND "
                "(f.valid_until IS NULL OR f.valid_until > ?) AND ("
                + direct_where
                + ")"
                + subject_where
                + " ORDER BY ("
                + score
                + ") DESC, "
                "f.valid_from DESC, f.created_at DESC, f.id LIMIT ?",
                (
                    *allowed_scopes,
                    at,
                    at,
                    *direct_params,
                    *subject_params,
                    *direct_params,
                    _FACT_RECALL_LIMIT,
                ),
            )
            selected = await cursor.fetchall()
        return [_row_to_fact(row) for row in selected]

    async def recent(
        self,
        limit: int = policy("memory", "facts.parameters.recent_limit", int),
        now: float | None = None,
        scope: str | None = None,
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
                "f.source_segment_id, f.source_chunk_id, "
                "f.created_at, f.polarity, f.scope, s.entity_type AS subject_type, "
                "o.entity_type AS object_type "
                "FROM memory_fact f "
                "JOIN memory_entity s ON s.id = f.subject_entity_id "
                "LEFT JOIN memory_entity o ON o.id = f.object_entity_id "
                "WHERE f.valid_from <= ? AND "
                "(f.valid_until IS NULL OR f.valid_until > ?) "
                + ("AND f.scope = ? " if scope is not None else "")
                + "ORDER BY f.created_at DESC, f.valid_from DESC, f.id LIMIT ?",
                (at, at, scope, bounded) if scope is not None else (at, at, bounded),
            )
            rows = await cursor.fetchall()
        return [_row_to_fact(row) for row in rows]

    async def _ensure_entity(
        self, name: str, entity_type: str = "concept", aliases: tuple[str, ...] = ()
    ) -> str:
        normalized = _normalize_entity_name(name)
        type_name = (
            entity_type.strip()[
                : policy(
                    "memory",
                    "facts.parameters.ensure_entity_type_name_chars_or_items",
                    int,
                )
            ]
            or "concept"
        )
        normalized_aliases = tuple(
            dict.fromkeys(
                alias
                for alias in (_normalize_entity_name(value) for value in aliases)
                if alias
            )
        )[
            : policy(
                "memory",
                "facts.parameters.ensure_entity_normalized_aliases_chars_or_items",
                int,
            )
        ]
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
            row = await cursor.fetchone()
        if row is not None:
            current = json.loads(row["aliases"] or "[]")
            merged = list(dict.fromkeys([*current, *normalized_aliases]))[
                : policy(
                    "memory",
                    "facts.parameters.ensure_entity_merged_chars_or_items",
                    int,
                )
            ]
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
        scope: str,
    ) -> bool:
        cursor = await self._db.conn.execute(
            "SELECT 1 FROM memory_fact WHERE subject_entity_id = ? AND predicate = ? "
            "AND object_entity_id = ? AND object_value = ? AND valid_from = ? "
            "AND polarity = ? AND scope = ? LIMIT 1",
            (
                subject_id,
                predicate,
                object_id,
                object_value,
                valid_from,
                polarity,
                scope,
            ),
        )
        return await cursor.fetchone() is not None

    async def _remove_same_start_conflicts(
        self,
        subject_id: str,
        predicate: str,
        valid_from: float,
        object_id: str,
        polarity: int,
        scope: str,
    ) -> None:
        await self._db.conn.execute(
            "DELETE FROM memory_fact WHERE subject_entity_id = ? "
            "AND predicate = ? AND valid_from = ? AND object_entity_id != ? "
            "AND scope = ?",
            (subject_id, predicate, valid_from, object_id, scope),
        )
        await self._db.conn.execute(
            "DELETE FROM memory_fact WHERE subject_entity_id = ? "
            "AND predicate = ? AND valid_from = ? AND object_entity_id = ? "
            "AND polarity != ? AND scope = ?",
            (subject_id, predicate, valid_from, object_id, polarity, scope),
        )

    async def _close_replaced(
        self, subject_id: str, predicate: str, valid_from: float, scope: str
    ) -> None:
        await self._db.conn.execute(
            "UPDATE memory_fact SET valid_until = ? "
            "WHERE subject_entity_id = ? AND predicate = ? "
            "AND valid_from < ? AND (valid_until IS NULL OR valid_until > ?) "
            "AND scope = ?",
            (valid_from, subject_id, predicate, valid_from, valid_from, scope),
        )

    async def _next_fact_start(
        self,
        subject_id: str,
        predicate: str,
        valid_from: float,
        valid_until: float | None,
        scope: str,
    ) -> float | None:
        """Bound a late-arriving historical fact before the next known state."""
        cursor = await self._db.conn.execute(
            "SELECT MIN(valid_from) AS next_start FROM memory_fact "
            "WHERE subject_entity_id = ? AND predicate = ? AND valid_from > ? "
            "AND scope = ?",
            (subject_id, predicate, valid_from, scope),
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
        source_segment_id=(
            str(row["source_segment_id"])
            if "source_segment_id" in row.keys()
            and row["source_segment_id"] is not None
            else None
        ),
        source_chunk_id=(
            str(row["source_chunk_id"]) if row["source_chunk_id"] is not None else None
        ),
        created_at=float(row["created_at"]),
        polarity=int(row["polarity"]) if "polarity" in row.keys() else 1,
        subject_type=(
            str(row["subject_type"])
            if "subject_type" in row.keys() and row["subject_type"] is not None
            else "person"
        ),
        object_type=(
            str(row["object_type"])
            if "object_type" in row.keys() and row["object_type"] is not None
            else "concept"
        ),
        scope=(
            str(row["scope"])
            if "scope" in row.keys() and row["scope"] is not None
            else "conversation"
        ),
    )


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
