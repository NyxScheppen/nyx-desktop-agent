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
from nyx.types import Book, BookListItem, Paragraph, ReadingProgress

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

    # ---- 20-reading-progress：进度 / 书架 / 分页 ----

    async def find_book(self, book_id: str) -> Book | None:
        """按 book_id 单行查，供 facade 判书是否存在。"""
        async with self._db.lock:
            cursor = await self._db.conn.execute(
                f"SELECT {_COLS} FROM books WHERE id = ?", (book_id,),
            )
            row = await cursor.fetchone()
            return _row_to_book(row) if row is not None else None

    async def list_books(self) -> list[BookListItem]:
        """书架列表：books LEFT JOIN reading_progress，已读排前、未读排后。

        `user_position = COALESCE(p.user_position, 0)`（未读 0 哨兵）、
        `last_read_at = p.updated_at`（未读 None）；排序取 `p.updated_at`。
        """
        async with self._db.lock:
            cursor = await self._db.conn.execute(
                "SELECT b.id, b.title, b.author, b.filename, b.total_paragraphs, "
                "COALESCE(p.user_position, 0) AS user_position, "
                "p.updated_at AS last_read_at "
                "FROM books b LEFT JOIN reading_progress p ON p.book_id = b.id "
                "ORDER BY (p.updated_at IS NULL) ASC, p.updated_at DESC, "
                "b.created_at DESC",
            )
            rows = await cursor.fetchall()
            return [_row_to_list_item(r) for r in rows]

    async def list_paragraphs(
        self, book_id: str, from_idx: int, to_idx: int
    ) -> list[Paragraph]:
        """读 [from_idx, to_idx] 闭区间段落；is_chapter_start 还原 bool。"""
        async with self._db.lock:
            cursor = await self._db.conn.execute(
                'SELECT id, book_id, "index", text, is_chapter_start '
                "FROM paragraphs WHERE book_id = ? AND \"index\" BETWEEN ? AND ? "
                'ORDER BY "index" ASC',
                (book_id, from_idx, to_idx),
            )
            rows = await cursor.fetchall()
            return [_row_to_paragraph(r) for r in rows]

    async def get_progress(self, book_id: str) -> ReadingProgress | None:
        """读进度单行；无记录返回 None（默认值由 facade 补）。"""
        async with self._db.lock:
            cursor = await self._db.conn.execute(
                "SELECT book_id, user_position, nyx_position, reading_speed, "
                "read_count, updated_at FROM reading_progress WHERE book_id = ?",
                (book_id,),
            )
            row = await cursor.fetchone()
            return _row_to_progress(row) if row is not None else None

    async def upsert_progress(
        self, book_id: str, user_position: int, nyx_position: int, reading_speed: int
    ) -> ReadingProgress:
        """写进度 UPSERT；不碰 read_count（重读计数只由 22 ++）。"""
        async with self._db.lock:
            now = time.time()
            await self._db.conn.execute(
                "INSERT INTO reading_progress "
                "(book_id, user_position, nyx_position, reading_speed, updated_at) "
                "VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(book_id) DO UPDATE SET "
                "user_position = excluded.user_position, "
                "nyx_position = excluded.nyx_position, "
                "reading_speed = excluded.reading_speed, "
                "updated_at = excluded.updated_at",
                (book_id, user_position, nyx_position, reading_speed, now),
            )
            await self._db.conn.commit()
            return await self._get_progress_locked(book_id)

    async def increment_read_count(self, book_id: str) -> ReadingProgress:
        """整本读完 ++（无行建默认行 read_count=1，position/speed 走 DDL DEFAULT）。"""
        async with self._db.lock:
            now = time.time()
            await self._db.conn.execute(
                "INSERT INTO reading_progress (book_id, read_count, updated_at) "
                "VALUES (?, 1, ?) "
                "ON CONFLICT(book_id) DO UPDATE SET "
                "read_count = read_count + 1, updated_at = excluded.updated_at",
                (book_id, now),
            )
            await self._db.conn.commit()
            return await self._get_progress_locked(book_id)

    async def _get_progress_locked(self, book_id: str) -> ReadingProgress:
        """读进度单行（供 upsert/increment 写后回读）；调用方须已持锁。"""
        cursor = await self._db.conn.execute(
            "SELECT book_id, user_position, nyx_position, reading_speed, "
            "read_count, updated_at FROM reading_progress WHERE book_id = ?",
            (book_id,),
        )
        row = await cursor.fetchone()
        if row is None:  # 写路径刚 INSERT/UPDATE，行必在；避免 -O 下 assert 被剥离
            raise RuntimeError(f"写后回读缺失：{book_id}")
        return _row_to_progress(row)


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


def _row_to_list_item(row: aiosqlite.Row) -> BookListItem:
    return BookListItem(
        id=row["id"],
        title=row["title"],
        author=row["author"],
        filename=row["filename"],
        total_paragraphs=row["total_paragraphs"],
        user_position=row["user_position"],
        last_read_at=row["last_read_at"],
    )


def _row_to_paragraph(row: aiosqlite.Row) -> Paragraph:
    return Paragraph(
        id=row["id"],
        book_id=row["book_id"],
        index=row["index"],
        text=row["text"],
        is_chapter_start=bool(row["is_chapter_start"]),
    )


def _row_to_progress(row: aiosqlite.Row) -> ReadingProgress:
    return ReadingProgress(
        book_id=row["book_id"],
        user_position=row["user_position"],
        nyx_position=row["nyx_position"],
        reading_speed=row["reading_speed"],
        read_count=row["read_count"],
        updated_at=row["updated_at"],
    )
