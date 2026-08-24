import json

import aiosqlite

from nyx.db import Database
from nyx.enums import MemoryType
from nyx.types import Memory, MemoryEdge

_MEMORY_COLS = (
    "id, created_at, content, tag, summary, freshness, "
    "type, recall_count, aspect, embedding"
)


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
                f"INSERT INTO memory ({_MEMORY_COLS}) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                _memory_row(memory),
            )
            await self._db.conn.commit()

    async def get(self, memory_id: str) -> Memory | None:
        async with self._db.lock:
            cursor = await self._db.conn.execute(
                f"SELECT {_MEMORY_COLS} FROM memory WHERE id = ?", (memory_id,),
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

    async def update(self, memory: Memory) -> None:
        """单条更新：委托批量版（UPDATE SQL 单一来源，单锁单 commit）。"""
        await self.update_many([memory])

    async def update_many(self, memories: list[Memory]) -> None:
        """批量更新：循环 UPDATE，单锁单 commit（衰减结算用，避免 N 次 commit）。"""
        async with self._db.lock:
            for m in memories:
                await self._db.conn.execute(
                    "UPDATE memory SET content = ?, tag = ?, summary = ?, "
                    "freshness = ?, type = ?, recall_count = ?, aspect = ?, "
                    "embedding = ? WHERE id = ?",
                    (
                        m.content, m.tag, m.summary, m.freshness,
                        m.type.value, m.recall_count, json.dumps(m.aspect),
                        _embedding_json(m.embedding),
                        m.id,
                    ),
                )
            await self._db.conn.commit()

    async def delete(self, memory_id: str) -> None:
        """单条删除：委托批量版（edge + row 删除单一来源，单锁单 commit）。"""
        await self.delete_many([memory_id])

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

    async def search_keyword(self, query: str) -> list[Memory]:
        pattern = f"%{_escape_like(query)}%"
        async with self._db.lock:
            cursor = await self._db.conn.execute(
                f"SELECT {_MEMORY_COLS} FROM memory "
                "WHERE content LIKE ? ESCAPE '\\' OR summary LIKE ? ESCAPE '\\' "
                "ORDER BY freshness DESC, created_at DESC",
                (pattern, pattern),
            )
            rows = await cursor.fetchall()
        return [_row_to_memory(r) for r in rows]

    async def list_edges(self) -> list[MemoryEdge]:
        async with self._db.lock:
            cursor = await self._db.conn.execute(
                "SELECT from_id, to_id, weight FROM memory_edge",
            )
            rows = await cursor.fetchall()
        return [
            MemoryEdge(from_id=r["from_id"], to_id=r["to_id"], weight=r["weight"])
            for r in rows
        ]

    async def upsert_edge(self, from_id: str, to_id: str, weight: float) -> None:
        async with self._db.lock:
            await self._db.conn.execute(
                "INSERT INTO memory_edge (from_id, to_id, weight) VALUES (?, ?, ?) "
                "ON CONFLICT(from_id, to_id) DO UPDATE SET weight = excluded.weight",
                (from_id, to_id, weight),
            )
            await self._db.conn.commit()


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
