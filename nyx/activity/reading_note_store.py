import aiosqlite

from nyx.db import Database
from nyx.types import Annotation, ReadingNote

_NOTE_COLS = "id, book, content, created_at, path"
_ANNOTATION_COLS = "id, target_id, author, content, created_at"


class ReadingNoteStore:
    """读书笔记 + 批注两张表 CRUD：读完一本落一条笔记，用户可删笔记、加批注。

    与 MaterialStore 同层（store 层）；所有读写 `async with self._db.lock:`
    串行化（同 05/07/11）。删除笔记级联删其批注（同一事务）。
    """

    def __init__(self, db: Database) -> None:
        self._db = db

    async def upsert_by_path(self, note: ReadingNote) -> None:
        """按 path 去重落笔记：命中则原地更新（保留 note id → 批注仍挂旧 id），
        未命中则插入。单锁内 SELECT + UPDATE/INSERT 原子完成，避免跨路径同名
        误删、也避免重读静默删用户批注。"""
        async with self._db.lock:
            cursor = await self._db.conn.execute(
                "SELECT id FROM reading_note WHERE path = ?", (note.path,)
            )
            row = await cursor.fetchone()
            if row is not None:
                await self._db.conn.execute(
                    "UPDATE reading_note SET book = ?, content = ?, created_at = ? "
                    "WHERE id = ?",
                    (note.book, note.content, note.created_at, row["id"]),
                )
            else:
                await self._db.conn.execute(
                    f"INSERT INTO reading_note ({_NOTE_COLS}) VALUES (?, ?, ?, ?, ?)",
                    (note.id, note.book, note.content, note.created_at, note.path),
                )
            await self._db.conn.commit()

    async def list_notes(self, limit: int = 50) -> list[ReadingNote]:
        """全量笔记（含 annotation_count 徽标用），按创建时间倒序。"""
        async with self._db.lock:
            cursor = await self._db.conn.execute(
                f"SELECT {_NOTE_COLS}, "
                "(SELECT COUNT(*) FROM annotation a "
                "WHERE a.target_id = reading_note.id) AS annotation_count "
                "FROM reading_note ORDER BY created_at DESC LIMIT ?",
                (limit,),
            )
            rows = await cursor.fetchall()
        return [_row_to_note(row) for row in rows]

    async def delete(self, note_id: str) -> None:
        """删一条笔记 + 其全部批注（同一事务；已落盘的 notes/*.md 文件不动）。"""
        async with self._db.lock:
            await self._db.conn.execute(
                "DELETE FROM annotation WHERE target_id = ?", (note_id,)
            )
            await self._db.conn.execute(
                "DELETE FROM reading_note WHERE id = ?", (note_id,)
            )
            await self._db.conn.commit()

    async def add_annotation(self, annotation: Annotation) -> None:
        """给某条笔记加一条批注。"""
        async with self._db.lock:
            await self._db.conn.execute(
                f"INSERT INTO annotation ({_ANNOTATION_COLS}) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    annotation.id,
                    annotation.target_id,
                    annotation.author,
                    annotation.content,
                    annotation.created_at,
                ),
            )
            await self._db.conn.commit()

    async def list_annotations(self, target_id: str) -> list[Annotation]:
        """某笔记的全部批注，按创建时间升序（早的在前）。"""
        async with self._db.lock:
            cursor = await self._db.conn.execute(
                f"SELECT {_ANNOTATION_COLS} FROM annotation "
                "WHERE target_id = ? ORDER BY created_at ASC",
                (target_id,),
            )
            rows = await cursor.fetchall()
        return [_row_to_annotation(row) for row in rows]

    async def delete_annotation(self, annotation_id: str) -> None:
        """删一条批注。"""
        async with self._db.lock:
            await self._db.conn.execute(
                "DELETE FROM annotation WHERE id = ?", (annotation_id,)
            )
            await self._db.conn.commit()


def _row_to_note(row: aiosqlite.Row) -> ReadingNote:
    return ReadingNote(
        id=row["id"],
        book=row["book"],
        content=row["content"],
        created_at=row["created_at"],
        path=row["path"],
        annotation_count=int(row["annotation_count"]),
    )


def _row_to_annotation(row: aiosqlite.Row) -> Annotation:
    return Annotation(
        id=row["id"],
        target_id=row["target_id"],
        author=row["author"],
        content=row["content"],
        created_at=row["created_at"],
    )
