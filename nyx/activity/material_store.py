import json

import aiosqlite

from nyx.db import Database
from nyx.types import Material

_COLS = "path, filename, total_chars, read_chars, created_at, updated_at"


class MaterialStore:
    """读物（书库）单表 CRUD：上传注册 + 分块进度 + 选最近未读完。

    与 ActivityStore 同层（store 层）；所有读写 `async with self._db.lock:`
    串行化（同 05/07/11）。
    """

    def __init__(self, db: Database) -> None:
        self._db = db

    async def upsert(
        self, path: str, filename: str, total_chars: int, now: float
    ) -> None:
        """注册（或重传覆盖）一本书：重传同路径重置进度为 0、更新时间戳。"""
        async with self._db.lock:
            await self._db.conn.execute(
                "INSERT INTO material (path, filename, total_chars, read_chars, "
                "created_at, updated_at) VALUES (?, ?, ?, 0, ?, ?) "
                "ON CONFLICT(path) DO UPDATE SET filename = excluded.filename, "
                "total_chars = excluded.total_chars, read_chars = 0, "
                "note_fragments = '[]', "
                "created_at = excluded.created_at, updated_at = excluded.updated_at",
                (path, filename, total_chars, now, now),
            )
            await self._db.conn.commit()

    async def next_readable(self) -> Material | None:
        """最近上传、且未读完的书（read_chars < total_chars，按 created_at 倒序）；
        无则 None。"""
        async with self._db.lock:
            cursor = await self._db.conn.execute(
                f"SELECT {_COLS} FROM material WHERE read_chars < total_chars "
                "ORDER BY created_at DESC LIMIT 1"
            )
            row = await cursor.fetchone()
        return _row_to_material(row) if row is not None else None

    async def find_by_topic(self, topic: str) -> Material | None:
        """按主题（filename 子串，SQLite LIKE 默认大小写不敏感）选一本未读完的书；
        无则 None。goal.topic（如「骑士团」）与「最近上传」可能不同，读书按 topic
        选料时优先走这里（C2）。"""
        async with self._db.lock:
            cursor = await self._db.conn.execute(
                f"SELECT {_COLS} FROM material WHERE filename LIKE ? "
                "AND read_chars < total_chars ORDER BY created_at DESC LIMIT 1",
                (f"%{topic}%",),
            )
            row = await cursor.fetchone()
        return _row_to_material(row) if row is not None else None

    async def advance(self, path: str, read_chars: int, now: float) -> None:
        """推进一本书的已读进度（updated_at 同步刷新）。"""
        async with self._db.lock:
            await self._db.conn.execute(
                "UPDATE material SET read_chars = ?, updated_at = ? WHERE path = ?",
                (read_chars, now, path),
            )
            await self._db.conn.commit()

    async def append_fragment(self, path: str, note: str, now: float) -> None:
        """追加一块片段笔记到 note_fragments（JSON 数组，updated_at 同步刷新）。"""
        async with self._db.lock:
            cursor = await self._db.conn.execute(
                "SELECT note_fragments FROM material WHERE path = ?", (path,)
            )
            row = await cursor.fetchone()
            fragments: list[str] = (
                json.loads(row["note_fragments"]) if row is not None else []
            )
            fragments.append(note)
            await self._db.conn.execute(
                "UPDATE material SET note_fragments = ?, updated_at = ? WHERE path = ?",
                (json.dumps(fragments, ensure_ascii=False), now, path),
            )
            await self._db.conn.commit()

    async def get_fragments(self, path: str) -> list[str]:
        """读一本书已累积的片段笔记（无则空列表）。"""
        async with self._db.lock:
            cursor = await self._db.conn.execute(
                "SELECT note_fragments FROM material WHERE path = ?", (path,)
            )
            row = await cursor.fetchone()
        if row is None:
            return []
        return json.loads(row["note_fragments"])


def _row_to_material(row: aiosqlite.Row) -> Material:
    return Material(
        path=row["path"],
        filename=row["filename"],
        total_chars=row["total_chars"],
        read_chars=row["read_chars"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )
