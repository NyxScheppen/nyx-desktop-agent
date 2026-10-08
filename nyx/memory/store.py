import hashlib
import json
import re
import time
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass

import aiosqlite

from nyx.db import Database
from nyx.enums import MemoryEdgeKind, MemoryKind, MemoryType
from nyx.types import Memory, MemoryEdge, ReadingEvidence

_MEMORY_COLS = (
    "id, created_at, content, kind, topics, summary, freshness, "
    "type, recall_count, aspect, embedding"
)
# INSERT 用：store 派生列不进入公开 Memory。
_MEMORY_INSERT_COLS = (
    _MEMORY_COLS + ", content_hash, first_created_at, freshness_updated_at"
)
_SOURCE_TOPIC_PREFIXES = ("book:", "material:", "web:", "local:")


@dataclass
class KeywordSearchHit:
    memory_id: str
    summary_tokens: list[str]
    content_tokens: list[str]


def hash_content(content: str) -> str:
    """Canonical content → SHA-256 hex exact-dedup key."""
    canonical = re.sub(r"\s+", " ", content.strip())
    canonical = canonical.replace("，", ",").replace("。", ".")
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class MemoryStore:
    """memory / memory_edge 两表的 SQLite 存取 + 行↔dataclass 序列化。

    db 由组合根注入（同所有 store 共享一个 conn+lock）。每个方法一个
    `async with db.lock` 的 SQL 块，锁作用域 = 单方法内、不跨方法嵌套
    （asyncio.Lock 不可重入，嵌套死锁）。
    """

    def __init__(self, db: Database) -> None:
        self._db = db

    @property
    def db(self) -> Database:
        """Return the shared database for local transaction orchestration."""
        return self._db

    @asynccontextmanager
    async def _operation(self) -> AsyncGenerator[bool, None]:
        """Yield whether this method owns the commit for its SQL block."""
        if self._db.in_transaction:
            yield False
            return
        async with self._db.lock:
            yield True

    async def add(self, memory: Memory) -> None:
        async with self._operation() as should_commit:
            await self._db.conn.execute(
                f"INSERT INTO memory ({_MEMORY_INSERT_COLS}) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (*_memory_row(memory), hash_content(memory.content),
                 memory.created_at, memory.created_at),
            )
            if should_commit:
                await self._db.conn.commit()

    async def get(self, memory_id: str) -> Memory | None:
        async with self._operation():
            cursor = await self._db.conn.execute(
                f"SELECT {_MEMORY_COLS} FROM memory WHERE id = ?", (memory_id,),
            )
            row = await cursor.fetchone()
        return _row_to_memory(row) if row is not None else None

    async def find_by_content(
        self,
        content: str,
        kind: MemoryKind,
        required_topic: str | None = None,
    ) -> Memory | None:
        """在同一 kind 内按 content 精确哈希查重。"""
        if required_topic is not None:
            topic_clause = (
                " AND EXISTS (SELECT 1 FROM json_each(memory.topics) "
                "WHERE json_each.value = ?)"
            )
        else:
            source_checks = " OR ".join(
                "json_each.value LIKE ?" for _ in _SOURCE_TOPIC_PREFIXES
            )
            topic_clause = (
                " AND NOT EXISTS (SELECT 1 FROM json_each(memory.topics) WHERE "
                f"{source_checks})"
            )
        params: tuple[str, ...] = (kind.value, hash_content(content))
        if required_topic is not None:
            params = (*params, required_topic)
        else:
            params = (*params, *(f"{prefix}%" for prefix in _SOURCE_TOPIC_PREFIXES))
        async with self._operation():
            cursor = await self._db.conn.execute(
                f"SELECT {_MEMORY_COLS} FROM memory "
                f"WHERE kind = ? AND content_hash = ?{topic_clause}",
                params,
            )
            row = await cursor.fetchone()
        return _row_to_memory(row) if row is not None else None

    async def list_memories(
        self,
        kind: MemoryKind | None = None,
        type: MemoryType | None = None,
        limit: int | None = None,
    ) -> list[Memory]:
        clauses: list[str] = []
        params: list[str] = []
        if kind is not None:
            clauses.append("kind = ?")
            params.append(kind.value)
        if type is not None:
            clauses.append("type = ?")
            params.append(type.value)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        sql = (
            f"SELECT {_MEMORY_COLS} FROM memory{where} "
            "ORDER BY freshness DESC, created_at DESC"
        )
        if limit is not None:
            sql += f" LIMIT {limit}"
        async with self._operation():
            cursor = await self._db.conn.execute(sql, params)
            rows = await cursor.fetchall()
        return [_row_to_memory(r) for r in rows]

    async def update_many(self, memories: list[Memory]) -> None:
        """批量更新：循环 UPDATE，单锁单 commit（衰减结算用，避免 N 次 commit）。"""
        async with self._operation() as should_commit:
            for m in memories:
                await self._db.conn.execute(
                    "UPDATE memory SET content = ?, kind = ?, topics = ?, summary = ?, "
                    "freshness = ?, type = ?, recall_count = ?, aspect = ?, "
                    "embedding = ?, content_hash = ? WHERE id = ?",
                    (
                        m.content, m.kind.value, json.dumps(m.topics),
                        m.summary, m.freshness,
                        m.type.value, m.recall_count, json.dumps(m.aspect),
                        _embedding_json(m.embedding),
                        hash_content(m.content),
                        m.id,
                    ),
                )
            if should_commit:
                await self._db.conn.commit()

    async def delete_many(self, ids: list[str]) -> None:
        """批量删除：循环删 edge/row，单锁单 commit（淘汰溢出，避免 N 次 commit）。"""
        async with self._operation() as should_commit:
            for memory_id in ids:
                await self._db.conn.execute(
                    "DELETE FROM memory_edge WHERE from_id = ? OR to_id = ?",
                    (memory_id, memory_id),
                )
                await self._db.conn.execute(
                    "DELETE FROM memory WHERE id = ?", (memory_id,)
                )
            if should_commit:
                await self._db.conn.commit()

    async def record_recall(
        self, memory_id: str, user_event_id: str, promote_threshold: int
    ) -> bool:
        """原子记录事件级召回；首次使用才计数并按阈值升级。

        返回是否升级（供 facade 发 memory_promoted）。阈值由 facade 传入——
        策略仍在 facade，store 只提供「幂等 marker + 加一 + 条件升型」原语。
        """
        async with self._operation() as should_commit:
            marker = await self._db.conn.execute(
                "INSERT OR IGNORE INTO memory_recall_use "
                "(user_event_id, memory_id) "
                "SELECT ?, id FROM memory WHERE id = ?",
                (user_event_id, memory_id),
            )
            if marker.rowcount != 1:
                if should_commit:
                    await self._db.conn.commit()
                return False
            await self._db.conn.execute(
                "UPDATE memory SET recall_count = recall_count + 1 WHERE id = ?",
                (memory_id,),
            )
            cursor = await self._db.conn.execute(
                "UPDATE memory SET type = ? WHERE id = ? AND type = ? "
                "AND recall_count >= ?",
                (
                    MemoryType.LONG_TERM.value,
                    memory_id,
                    MemoryType.SHORT_TERM.value,
                    promote_threshold,
                ),
            )
            promoted = cursor.rowcount == 1
            if should_commit:
                await self._db.conn.commit()
        return promoted

    async def count_new(self, kind: MemoryKind | None, since: float) -> int:
        """计数「首次创建晚于 since 的 kind 记忆」，不物化整行/embedding。

        用 first_created_at（INSERT 时定格、strengthen/update_many 不刷新）。
        kind=None 表示全量计数，供定时反思判断是否有足够新记忆。
        """
        async with self._operation():
            if kind is None:
                cursor = await self._db.conn.execute(
                    "SELECT COUNT(*) FROM memory WHERE first_created_at > ?",
                    (since,),
                )
            else:
                cursor = await self._db.conn.execute(
                    "SELECT COUNT(*) FROM memory "
                    "WHERE kind = ? AND first_created_at > ?",
                    (kind.value, since),
                )
            row = await cursor.fetchone()
        return int(row[0]) if row is not None else 0

    async def strengthen(self, memory_id: str, now: float) -> None:
        """重复写入合并强化：freshness 重置，不冒充慢通道召回。

        created_at 是创建时间，不随强化刷新；freshness 从强化时刻重新衰减。
        """
        async with self._operation() as should_commit:
            await self._db.conn.execute(
                "UPDATE memory SET freshness = 1.0, freshness_updated_at = ? "
                "WHERE id = ?",
                (now, memory_id),
            )
            if should_commit:
                await self._db.conn.commit()

    async def record_reading_evidence(
        self,
        source_topic: str,
        source_name: str,
        content: str,
        block_key: str,
        *,
        book_id: str | None = None,
    ) -> None:
        """Persist exact source exposure once, including pending replay."""
        if not content.strip():
            return
        if not source_topic or len(content) > 6000:
            raise ValueError("阅读证据需要来源且正文不能超过 6000 字符")
        key = json.dumps([source_topic, block_key, content], ensure_ascii=False)
        evidence_id = hashlib.sha256(key.encode("utf-8")).hexdigest()
        async with self._operation() as should_commit:
            await self._db.conn.execute(
                "INSERT INTO reading_evidence "
                "(id, source_topic, source_name, content, created_at, book_id) "
                "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(id) DO NOTHING",
                (evidence_id, source_topic, source_name, content, time.time(), book_id),
            )
            if should_commit:
                await self._db.conn.commit()

    async def pending_reading_evidence(self, limit: int = 3) -> list[ReadingEvidence]:
        """Return bounded oldest unconsumed source snapshots."""
        if not 1 <= limit <= 3:
            raise ValueError("阅读证据 limit 必须在 [1, 3]")
        async with self._operation():
            cursor = await self._db.conn.execute(
                "SELECT id, source_topic, source_name, content, created_at "
                "FROM reading_evidence WHERE consumed_at IS NULL "
                "ORDER BY created_at, id LIMIT ?", (limit,),
            )
            rows = await cursor.fetchall()
        return [ReadingEvidence(
            id=row["id"], source_topic=row["source_topic"],
            source_name=row["source_name"], content=row["content"],
            created_at=row["created_at"],
        ) for row in rows]

    async def consume_reading_evidence(self, ids: list[str]) -> None:
        """Consume a prepared snapshot inside the caller's reflection transaction."""
        if not self._db.in_transaction:
            raise RuntimeError("阅读证据消费必须处于反思事务内")
        unique_ids = list(dict.fromkeys(ids))
        if not unique_ids:
            return
        placeholders = ",".join("?" for _ in unique_ids)
        cursor = await self._db.conn.execute(
            "UPDATE reading_evidence SET consumed_at = ? "
            f"WHERE consumed_at IS NULL AND id IN ({placeholders})",
            (time.time(), *unique_ids),
        )
        if cursor.rowcount != len(unique_ids):
            raise RuntimeError("阅读证据已删除或已消费，需要重新准备反思")

    async def settle_freshness(self, now: float, rate: float) -> None:
        """Apply only elapsed freshness decay since the prior settlement."""
        async with self._operation() as should_commit:
            await self._db.conn.execute(
                "UPDATE memory SET freshness = MAX(0.0, freshness - ? * "
                "((? - freshness_updated_at) / 86400.0)), "
                "freshness_updated_at = ? WHERE freshness_updated_at < ?",
                (rate, now, now, now),
            )
            if should_commit:
                await self._db.conn.commit()

    async def search_keywords(
        self,
        tokens: list[str],
        limit: int,
        required_topic: str | None = None,
    ) -> dict[str, KeywordSearchHit]:
        if not tokens or limit <= 0:
            return {}

        hits: dict[str, KeywordSearchHit] = {}
        freshness: dict[str, float] = {}
        created_at: dict[str, float] = {}
        async with self._operation():
            for token in tokens:
                pattern = f"%{_escape_like(token)}%"
                topic_clause = (
                    " AND EXISTS (SELECT 1 FROM json_each(memory.topics) "
                    "WHERE json_each.value = ?)"
                    if required_topic is not None
                    else ""
                )
                params: tuple[str, ...] = (pattern, pattern, pattern, pattern)
                if required_topic is not None:
                    params = (*params, required_topic)
                cursor = await self._db.conn.execute(
                    "SELECT id, freshness, created_at, "
                    "summary LIKE ? ESCAPE '\\' AS summary_hit, "
                    "content LIKE ? ESCAPE '\\' AS content_hit "
                    "FROM memory "
                    "WHERE (summary LIKE ? ESCAPE '\\' "
                    "OR content LIKE ? ESCAPE '\\')"
                    f"{topic_clause}",
                    params,
                )
                rows = await cursor.fetchall()
                for row in rows:
                    memory_id = row["id"]
                    hit = hits.setdefault(
                        memory_id,
                        KeywordSearchHit(memory_id, [], []),
                    )
                    freshness[memory_id] = row["freshness"]
                    created_at[memory_id] = row["created_at"]
                    if row["summary_hit"] and token not in hit.summary_tokens:
                        hit.summary_tokens.append(token)
                    if row["content_hit"] and token not in hit.content_tokens:
                        hit.content_tokens.append(token)

        sorted_hits = sorted(
            hits.values(),
            key=lambda hit: (
                -len(set(hit.summary_tokens) | set(hit.content_tokens)),
                -len(hit.summary_tokens),
                -len(hit.content_tokens),
                -freshness[hit.memory_id],
                -created_at[hit.memory_id],
                hit.memory_id,
            ),
        )
        return {hit.memory_id: hit for hit in sorted_hits[:limit]}

    async def list_edges(
        self,
        kind: MemoryEdgeKind | None = None,
    ) -> list[MemoryEdge]:
        params: tuple[str, ...] = ()
        where = ""
        if kind is not None:
            where = "WHERE kind = ? "
            params = (kind.value,)
        async with self._operation():
            cursor = await self._db.conn.execute(
                "SELECT from_id, to_id, kind, weight, created_at FROM memory_edge "
                f"{where}ORDER BY from_id ASC, to_id ASC, kind ASC",
                params,
            )
            rows = await cursor.fetchall()
        return [_row_to_edge(r) for r in rows]

    async def upsert_edge(
        self,
        from_id: str,
        to_id: str,
        kind: MemoryEdgeKind,
        weight: float,
        created_at: float,
    ) -> None:
        left, right = _canonical_edge_ids(from_id, to_id)
        async with self._operation() as should_commit:
            await self._db.conn.execute(
                "INSERT INTO memory_edge (from_id, to_id, kind, weight, created_at) "
                "VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(from_id, to_id, kind) DO UPDATE SET "
                "weight = excluded.weight, created_at = excluded.created_at",
                (left, right, kind.value, weight, created_at),
            )
            if should_commit:
                await self._db.conn.commit()

    async def delete_edges(
        self,
        keys: list[tuple[str, str, MemoryEdgeKind]],
    ) -> None:
        async with self._operation() as should_commit:
            for from_id, to_id, kind in keys:
                await self._db.conn.execute(
                    "DELETE FROM memory_edge "
                    "WHERE from_id = ? AND to_id = ? AND kind = ?",
                    (from_id, to_id, kind.value),
                )
            if should_commit:
                await self._db.conn.commit()

    async def list_edge_degrees(
        self,
        memory_ids: list[str],
    ) -> dict[str, list[MemoryEdge]]:
        result: dict[str, list[MemoryEdge]] = {
            memory_id: [] for memory_id in memory_ids
        }
        async with self._operation():
            for memory_id in memory_ids:
                cursor = await self._db.conn.execute(
                    "SELECT from_id, to_id, kind, weight, created_at "
                    "FROM memory_edge "
                    "WHERE from_id = ? OR to_id = ? "
                    "ORDER BY from_id ASC, to_id ASC, kind ASC",
                    (memory_id, memory_id),
                )
                rows = await cursor.fetchall()
                result[memory_id] = [_row_to_edge(r) for r in rows]
        return result


def _memory_row(
    m: Memory,
) -> tuple[str, float, str, str, str, str, float, str, int, str, str | None]:
    return (
        m.id, m.created_at, m.content, m.kind.value, json.dumps(m.topics), m.summary,
        m.freshness, m.type.value, m.recall_count, json.dumps(m.aspect),
        _embedding_json(m.embedding),
    )


def _embedding_json(v: list[float] | None) -> str | None:
    """embedding 列可空：None → SQL NULL（非 "null" 字符串）。

    list → JSON 数组字符串。
    """
    return json.dumps(v) if v is not None else None


def _escape_like(query: str) -> str:
    """转义 LIKE 通配符（%/_/\\），让 query 按字面匹配。"""
    return query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _canonical_edge_ids(a: str, b: str) -> tuple[str, str]:
    return (a, b) if a < b else (b, a)


def _row_to_memory(row: aiosqlite.Row) -> Memory:
    return Memory(
        id=row["id"],
        created_at=row["created_at"],
        content=row["content"],
        kind=MemoryKind(row["kind"]),
        topics=json.loads(row["topics"]),
        summary=row["summary"],
        freshness=row["freshness"],
        type=MemoryType(row["type"]),
        recall_count=row["recall_count"],
        aspect=json.loads(row["aspect"]),
        embedding=(
            json.loads(row["embedding"]) if row["embedding"] is not None else None
        ),
    )


def _row_to_edge(row: aiosqlite.Row) -> MemoryEdge:
    return MemoryEdge(
        from_id=row["from_id"],
        to_id=row["to_id"],
        kind=MemoryEdgeKind(row["kind"]),
        weight=row["weight"],
        created_at=row["created_at"],
    )
