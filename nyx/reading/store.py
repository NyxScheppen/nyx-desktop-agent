"""books / paragraphs 两表的唯一写路径（spec 19）。

ReadingStore 由组合根注入 `Database`（共享 conn+lock），方法各自
`async with self._db.lock:` 串行化（同其它 store）。`paragraphs."index"`
是 SQLite 关键字，SQL 里必须加双引号。
"""

import time
from uuid import uuid4

import aiosqlite

from nyx.db import Database
from nyx.reading.segmenter import Segment
from nyx.types import Book

_COLS = (
    "id, title, author, filename, content_hash, total_paragraphs, "
    "created_at, updated_at"
)


class ReadingStore:
    """books / paragraphs 的 SQLite 存取；id/时间戳在写路径内生成。"""

    def __init__(self, db: Database) -> None:
        self._db = db

    async def insert_book(
        self,
        title: str,
        author: str,
        filename: str,
        content_hash: str,
        total_paragraphs: int,
    ) -> Book:
        """插入一本新书（id=uuid4、created_at/updated_at=now），返回完整 Book。"""
        book_id = str(uuid4())
        now = time.time()
        async with self._db.lock:
            await self._db.conn.execute(
                f"INSERT INTO books ({_COLS}) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    book_id, title, author, filename, content_hash,
                    total_paragraphs, now, now,
                ),
            )
            await self._db.conn.commit()
        return Book(
            id=book_id, title=title, author=author, filename=filename,
            content_hash=content_hash, total_paragraphs=total_paragraphs,
            created_at=now, updated_at=now,
        )

    async def insert_paragraphs(self, book_id: str, segments: list[Segment]) -> None:
        """批量落段：`"index"` 从 1 连续、`is_chapter_start` bool→1/0，单 commit。"""
        async with self._db.lock:
            for i, segment in enumerate(segments, start=1):
                await self._db.conn.execute(
                    'INSERT INTO paragraphs (id, book_id, "index", text, '
                    "is_chapter_start) VALUES (?, ?, ?, ?, ?)",
                    (
                        str(uuid4()), book_id, i, segment.text,
                        int(segment.is_chapter_start),
                    ),
                )
            await self._db.conn.commit()

    async def find_by_hash(self, content_hash: str) -> Book | None:
        """按正文哈希查重；命中返回已有书，未命中 None。"""
        async with self._db.lock:
            cursor = await self._db.conn.execute(
                f"SELECT {_COLS} FROM books WHERE content_hash = ?", (content_hash,),
            )
            row = await cursor.fetchone()
        return _row_to_book(row) if row is not None else None


def _row_to_book(row: aiosqlite.Row) -> Book:
    return Book(
        id=row["id"],
        title=row["title"],
        author=row["author"],
        filename=row["filename"],
        content_hash=row["content_hash"],
        total_paragraphs=row["total_paragraphs"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )
