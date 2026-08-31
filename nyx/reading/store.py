"""books / paragraphs 两表的唯一写路径（spec 19）。

ReadingStore 由组合根注入 `Database`（共享 conn+lock），方法各自
`async with self._db.lock:` 串行化（同其它 store）。`paragraphs."index"`
是 SQLite 关键字，SQL 里必须加双引号。

书+段在 `insert_book_with_paragraphs` 里单锁单事务原子写入：同事务内先插
books 再批量插 paragraphs（`executemany`），任一失败整体回滚，不留「空壳书」。
`content_hash` 靠 v8 唯一索引兜底，插入撞 UNIQUE 时回滚并返回已入库的那本。
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

    async def insert_book_with_paragraphs(
        self,
        title: str,
        author: str,
        filename: str,
        content_hash: str,
        segments: list[Segment],
    ) -> tuple[Book, bool]:
        """原子导入一本新书；`content_hash` 已存在则返回 `(已有书, False)`。

        单锁 + 单事务：books 与 paragraphs 一起提交或一起回滚。并发同哈希靠
        v8 唯一索引兜底——插入撞 UNIQUE 时回滚并返回已入库的那本。
        """
        async with self._db.lock:
            book_id = str(uuid4())
            now = time.time()
            book = Book(
                id=book_id, title=title, author=author, filename=filename,
                content_hash=content_hash, total_paragraphs=len(segments),
                created_at=now, updated_at=now,
            )
            await self._db.conn.execute("BEGIN")
            try:
                await self._db.conn.execute(
                    f"INSERT INTO books ({_COLS}) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        book_id, title, author, filename, content_hash,
                        len(segments), now, now,
                    ),
                )
                await self._insert_paragraphs(book_id, segments)
            except aiosqlite.IntegrityError:
                await self._db.conn.rollback()
                existing = await self._find_by_hash_locked(content_hash)
                if existing is not None:
                    return existing, False
                raise
            except BaseException:
                await self._db.conn.rollback()
                raise
            else:
                await self._db.conn.commit()
        return book, True

    async def _insert_paragraphs(
        self, book_id: str, segments: list[Segment]
    ) -> None:
        """批量落段（`executemany`，一次往返）；调用方须已持锁 + 在事务内。"""
        await self._db.conn.executemany(
            'INSERT INTO paragraphs (id, book_id, "index", text, '
            "is_chapter_start) VALUES (?, ?, ?, ?, ?)",
            [
                (str(uuid4()), book_id, i, segment.text, int(segment.is_chapter_start))
                for i, segment in enumerate(segments, start=1)
            ],
        )

    async def find_by_hash(self, content_hash: str) -> Book | None:
        """按正文哈希查重；命中返回已有书，未命中 None。"""
        async with self._db.lock:
            return await self._find_by_hash_locked(content_hash)

    async def _find_by_hash_locked(self, content_hash: str) -> Book | None:
        """查重查询；调用方须已持锁（不嵌套持锁，见 store 锁作用域约定）。"""
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
