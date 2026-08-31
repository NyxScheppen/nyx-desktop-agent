"""阅读内容导入门面（spec 19）：EPUB 字节 → 去重 → 落库。

`parse_epub` 是同步 CPU 阻塞调用，用 `asyncio.to_thread` 卸载，不阻塞事件循环。
P1 只注入 `ReadingStore`（21/22 各自追加依赖时同步扩构造签名）。
"""

import asyncio

from nyx.reading.epub import parse_epub
from nyx.reading.store import ReadingStore
from nyx.types import Book, BookListItem, Paragraph, ReadingProgress


class DuplicateBookError(Exception):
    """正文重复导入（`content_hash` 命中已有书）；端点据此映射 409。"""

    def __init__(self, existing_book_id: str, title: str) -> None:
        self.existing_book_id = existing_book_id
        self.title = title
        super().__init__(f"已存在同内容书籍：{title}（{existing_book_id}）")


class BookNotFoundError(Exception):
    """书不存在（`book_id` 查无此书）；端点据此映射 404。"""

    def __init__(self, book_id: str) -> None:
        self.book_id = book_id
        super().__init__(f"书不存在：{book_id}")


class ReadingFacade:
    """陪读内容导入入口：`import_book(filename, data) -> Book`。"""

    def __init__(self, store: ReadingStore) -> None:
        self._store = store

    async def import_book(self, filename: str, data: bytes) -> Book:
        """解析 EPUB → 去重 → 插入 books + paragraphs → 返回 Book。

        title 缺失回退 filename（`parse_epub` 只拿 bytes、不知文件名）；
        正文重复抛 `DuplicateBookError`；空正文抛 `ValueError`（不插书）。
        """
        result = await asyncio.to_thread(parse_epub, data)
        if not result.segments:
            raise ValueError("EPUB 无正文")
        title = result.title or filename
        book, inserted = await self._store.insert_book_with_paragraphs(
            title, result.author, filename, result.content_hash, result.segments
        )
        if not inserted:
            raise DuplicateBookError(book.id, book.title)
        return book

    # ---- 20-reading-progress：进度 / 书架 / 分页 ----

    async def list_books(self) -> list[BookListItem]:
        """书架列表（直通 store；列表本身不需要某本书存在，故不判书存在）。"""
        return await self._store.list_books()

    async def list_paragraphs(
        self, book_id: str, from_idx: int, to_idx: int
    ) -> list[Paragraph]:
        """读段落范围；书不存在抛 `BookNotFoundError`，`to_idx` 越界抛 `ValueError`。"""
        book = await self._store.find_book(book_id)
        if book is None:
            raise BookNotFoundError(book_id)
        if to_idx > book.total_paragraphs:
            raise ValueError("段落越界")
        return await self._store.list_paragraphs(book_id, from_idx, to_idx)

    async def get_progress(self, book_id: str) -> ReadingProgress:
        """读进度；书不存在抛 `BookNotFoundError`，无进度行返回默认进度。"""
        book = await self._store.find_book(book_id)
        if book is None:
            raise BookNotFoundError(book_id)
        progress = await self._store.get_progress(book_id)
        if progress is None:
            return ReadingProgress(book_id, 1, 1, 50, 0, 0.0)
        return progress

    async def save_progress(
        self, book_id: str, user_position: int, nyx_position: int, reading_speed: int
    ) -> ReadingProgress:
        """写进度（委托 store 的 UPSERT）；书不存在抛 `BookNotFoundError`。"""
        book = await self._store.find_book(book_id)
        if book is None:
            raise BookNotFoundError(book_id)
        return await self._store.upsert_progress(
            book_id, user_position, nyx_position, reading_speed
        )
