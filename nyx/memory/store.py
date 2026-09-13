import hashlib
import json
from dataclasses import dataclass

import aiosqlite

from nyx.db import Database
from nyx.enums import MemoryEdgeKind, MemoryType
from nyx.types import Memory, MemoryEdge

_MEMORY_COLS = (
    "id, created_at, content, tag, summary, freshness, "
    "type, recall_count, aspect, embedding"
)
# INSERT 用：比 SELECT 多 content_hash + first_created_at（store 派生，Memory 不承载）
_MEMORY_INSERT_COLS = _MEMORY_COLS + ", content_hash, first_created_at"


@dataclass
class KeywordSearchHit:
    memory_id: str
    summary_tokens: list[str]
    content_tokens: list[str]


def hash_content(content: str) -> str:
    """content → SHA-256 hex 精确哈希（去重键）。纯函数。"""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


class MemoryStore:
    """memory / memory_edge 两表的 SQLite 存取 + 行↔dataclass 序列化。

    db 由组合根注入（同所有 store 共享一个 conn+lock）。每个方法一个
    `async with db.lock` 的 SQL 块，锁作用域 = 单方法内、不跨方法嵌套
    （asyncio.Lock 不可重入，嵌套死锁）。
    """

    def __init__(self, db: Database) -> None:
        self._db = db

    async def add(self, memory: Memory) -> None:
        async with self._db.lock:
            await self._db.conn.execute(
                f"INSERT INTO memory ({_MEMORY_INSERT_COLS}) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (*_memory_row(memory), hash_content(memory.content),
                 memory.created_at),
            )
            await self._db.conn.commit()

    async def get(self, memory_id: str) -> Memory | None:
        async with self._db.lock:
            cursor = await self._db.conn.execute(
                f"SELECT {_MEMORY_COLS} FROM memory WHERE id = ?", (memory_id,),
            )
            row = await cursor.fetchone()
        return _row_to_memory(row) if row is not None else None

    async def find_by_content(self, content: str) -> Memory | None:
        """按 content 精确哈希查重：命中返回已有记忆，未命中 None。"""
        async with self._db.lock:
            cursor = await self._db.conn.execute(
                f"SELECT {_MEMORY_COLS} FROM memory WHERE content_hash = ?",
                (hash_content(content),),
            )
            row = await cursor.fetchone()
        return _row_to_memory(row) if row is not None else None

    async def list_memories(
        self,
        tag: str | None = None,
        type: MemoryType | None = None,
        limit: int | None = None,
    ) -> list[Memory]:
        clauses: list[str] = []
        params: list[str] = []
        if tag is not None:
            clauses.append("tag = ?")
            params.append(tag)
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
        async with self._db.lock:
            cursor = await self._db.conn.execute(sql, params)
            rows = await cursor.fetchall()
        return [_row_to_memory(r) for r in rows]

    async def update_many(self, memories: list[Memory]) -> None:
        """批量更新：循环 UPDATE，单锁单 commit（衰减结算用，避免 N 次 commit）。"""
        async with self._db.lock:
            for m in memories:
                await self._db.conn.execute(
                    "UPDATE memory SET content = ?, tag = ?, summary = ?, "
                    "freshness = ?, type = ?, recall_count = ?, aspect = ?, "
                    "embedding = ?, content_hash = ? WHERE id = ?",
                    (
                        m.content, m.tag, m.summary, m.freshness,
                        m.type.value, m.recall_count, json.dumps(m.aspect),
                        _embedding_json(m.embedding),
                        hash_content(m.content),
                        m.id,
                    ),
                )
            await self._db.conn.commit()

    async def delete_many(self, ids: list[str]) -> None:
        """批量删除：循环删 edge/row，单锁单 commit（淘汰溢出，避免 N 次 commit）。"""
        async with self._db.lock:
            for memory_id in ids:
                await self._db.conn.execute(
                    "DELETE FROM memory_edge WHERE from_id = ? OR to_id = ?",
                    (memory_id, memory_id),
                )
                await self._db.conn.execute(
                    "DELETE FROM memory WHERE id = ?", (memory_id,)
                )
            await self._db.conn.commit()

    async def record_recall(self, memory_id: str, promote_threshold: int) -> bool:
        """原子：recall_count+1；短期且达阈值则升长期（单锁，避免跨方法竞态）。

        返回是否升级（供 facade 发 memory_promoted）。阈值由 facade 传入——
        策略仍在 facade，store 只提供「加一 + 条件升型」原语。
        """
        async with self._db.lock:
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
            await self._db.conn.commit()
        return promoted

    async def count_new(self, tag: str | None, since: float) -> int:
        """计数「首次创建晚于 since 的 tag 记忆」，不物化整行/embedding。

        用 first_created_at（INSERT 时定格、strengthen/update_many 不刷新）。
        tag=None 表示全量计数，供定时反思判断是否有足够新记忆。
        """
        async with self._db.lock:
            if tag is None:
                cursor = await self._db.conn.execute(
                    "SELECT COUNT(*) FROM memory WHERE first_created_at > ?",
                    (since,),
                )
            else:
                cursor = await self._db.conn.execute(
                    "SELECT COUNT(*) FROM memory "
                    "WHERE tag = ? AND first_created_at > ?",
                    (tag, since),
                )
            row = await cursor.fetchone()
        return int(row[0]) if row is not None else 0

    async def strengthen(self, memory_id: str, now: float) -> None:
        """重复写入合并强化：freshness 重置、recall_count+1。

        created_at 是创建时间，不随强化刷新；升级仍只由 record_recall 的
        promote_threshold 原子路径负责。
        """
        del now
        async with self._db.lock:
            await self._db.conn.execute(
                "UPDATE memory SET freshness = 1.0, recall_count = recall_count + 1 "
                "WHERE id = ?",
                (memory_id,),
            )
            await self._db.conn.commit()

    async def search_keywords(
        self,
        tokens: list[str],
        limit: int,
    ) -> dict[str, KeywordSearchHit]:
        if not tokens or limit <= 0:
            return {}

        hits: dict[str, KeywordSearchHit] = {}
        freshness: dict[str, float] = {}
        created_at: dict[str, float] = {}
        async with self._db.lock:
            for token in tokens:
                pattern = f"%{_escape_like(token)}%"
                cursor = await self._db.conn.execute(
                    "SELECT id, freshness, created_at, "
                    "summary LIKE ? ESCAPE '\\' AS summary_hit, "
                    "content LIKE ? ESCAPE '\\' AS content_hit "
                    "FROM memory "
                    "WHERE summary LIKE ? ESCAPE '\\' "
                    "OR content LIKE ? ESCAPE '\\'",
                    (pattern, pattern, pattern, pattern),
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
        async with self._db.lock:
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
        async with self._db.lock:
            await self._db.conn.execute(
                "INSERT INTO memory_edge (from_id, to_id, kind, weight, created_at) "
                "VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(from_id, to_id, kind) DO UPDATE SET "
                "weight = excluded.weight, created_at = excluded.created_at",
                (left, right, kind.value, weight, created_at),
            )
            await self._db.conn.commit()

    async def delete_edges(
        self,
        keys: list[tuple[str, str, MemoryEdgeKind]],
    ) -> None:
        async with self._db.lock:
            for from_id, to_id, kind in keys:
                await self._db.conn.execute(
                    "DELETE FROM memory_edge "
                    "WHERE from_id = ? AND to_id = ? AND kind = ?",
                    (from_id, to_id, kind.value),
                )
            await self._db.conn.commit()

    async def list_edge_degrees(
        self,
        memory_ids: list[str],
    ) -> dict[str, list[MemoryEdge]]:
        result: dict[str, list[MemoryEdge]] = {
            memory_id: [] for memory_id in memory_ids
        }
        async with self._db.lock:
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
) -> tuple[str, float, str, str, str, float, str, int, str, str | None]:
    return (
        m.id, m.created_at, m.content, m.tag, m.summary,
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
        tag=row["tag"],
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
