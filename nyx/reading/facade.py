"""阅读内容导入门面（spec 19）：EPUB 字节 → 去重 → 落库。

`parse_epub` 是同步 CPU 阻塞调用，用 `asyncio.to_thread` 卸载，不阻塞事件循环。
P1 只注入 `ReadingStore`（21/22 各自追加依赖时同步扩构造签名）。
"""

import asyncio

from nyx.reading.epub import parse_epub
from nyx.reading.store import ReadingStore
from nyx.types import Book


class DuplicateBookError(Exception):
    """正文重复导入（`content_hash` 命中已有书）；端点据此映射 409。"""

    def __init__(self, existing_book_id: str, title: str) -> None:
        self.existing_book_id = existing_book_id
        self.title = title
        super().__init__(f"已存在同内容书籍：{title}（{existing_book_id}）")


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
