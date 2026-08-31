"""ReadingStore 单元测试（19-reading-content）：:memory: + 真 store。

store 是 books/paragraphs 唯一写路径，直接测其契约：`insert_book_with_paragraphs`
单锁单事务原子落书+段、`content_hash` 去重（唯一索引 + IntegrityError 回退）、
并发同哈希只出一本书、`find_by_hash` 命中/落空。
"""

import asyncio

import aiosqlite
import pytest

from nyx import db
from nyx.reading.segmenter import Segment
from nyx.reading.store import ReadingStore


async def _new_store() -> tuple[ReadingStore, db.Database]:
    database = await db.connect(":memory:")
    return ReadingStore(database), database


async def test_insert_book_with_paragraphs_returns_new() -> None:
    store, database = await _new_store()
    try:
        book, created = await store.insert_book_with_paragraphs(
            title="挪威的森林", author="村上春树", filename="nwsdl.epub",
            content_hash="a" * 64,
            segments=[
                Segment(text="第一章\n开头", is_chapter_start=True),
                Segment(text="正文", is_chapter_start=False),
                Segment(text="结尾", is_chapter_start=False),
            ],
        )
        cursor = await database.conn.execute(
            'SELECT "index", text, is_chapter_start FROM paragraphs '
            'WHERE book_id = ? ORDER BY "index"',
            (book.id,),
        )
        rows = list(await cursor.fetchall())
    finally:
        await database.conn.close()
    assert created is True
    assert book.title == "挪威的森林"
    assert book.total_paragraphs == 3
    assert [r["index"] for r in rows] == [1, 2, 3]
    assert [r["is_chapter_start"] for r in rows] == [1, 0, 0]
    assert rows[0]["text"] == "第一章\n开头"


async def test_insert_book_with_paragraphs_duplicate_returns_existing() -> None:
    store, database = await _new_store()
    try:
        first, created = await store.insert_book_with_paragraphs(
            title="t", author="a", filename="f1.epub", content_hash="h" * 64,
            segments=[Segment(text="正文", is_chapter_start=False)],
        )
        again, created_again = await store.insert_book_with_paragraphs(
            title="t2", author="a2", filename="f2.epub", content_hash="h" * 64,
            segments=[Segment(text="正文", is_chapter_start=False)],
        )
        cursor = await database.conn.execute("SELECT COUNT(*) AS n FROM books")
        row = await cursor.fetchone()
    finally:
        await database.conn.close()
    assert created is True
    assert created_again is False
    assert again.id == first.id
    assert row is not None and row["n"] == 1


async def test_insert_concurrent_same_hash_yields_single_book() -> None:
    store, database = await _new_store()
    try:
        results = await asyncio.gather(
            store.insert_book_with_paragraphs(
                title="t", author="a", filename="f1.epub", content_hash="h" * 64,
                segments=[Segment(text="正文", is_chapter_start=False)],
            ),
            store.insert_book_with_paragraphs(
                title="t", author="a", filename="f2.epub", content_hash="h" * 64,
                segments=[Segment(text="正文", is_chapter_start=False)],
            ),
        )
        cursor = await database.conn.execute("SELECT COUNT(*) AS n FROM books")
        row = await cursor.fetchone()
    finally:
        await database.conn.close()
    assert sum(1 for _, is_new in results if is_new) == 1
    assert row is not None and row["n"] == 1


async def test_insert_atomic_rolls_back_on_paragraphs_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, database = await _new_store()

    async def boom(book_id: str, segments: list[Segment]) -> None:
        del book_id, segments
        raise aiosqlite.Error("磁盘满")

    monkeypatch.setattr(store, "_insert_paragraphs", boom)
    try:
        with pytest.raises(aiosqlite.Error):
            await store.insert_book_with_paragraphs(
                title="t", author="a", filename="f", content_hash="h" * 64,
                segments=[Segment(text="正文", is_chapter_start=False)],
            )
        cursor = await database.conn.execute("SELECT COUNT(*) AS n FROM books")
        row = await cursor.fetchone()
    finally:
        await database.conn.close()
    assert row is not None and row["n"] == 0  # book 已回滚，无空壳


async def test_find_by_hash_hit() -> None:
    store, database = await _new_store()
    try:
        book, _ = await store.insert_book_with_paragraphs(
            title="t", author="a", filename="f", content_hash="h" * 64,
            segments=[Segment(text="正文", is_chapter_start=False)],
        )
        found = await store.find_by_hash("h" * 64)
    finally:
        await database.conn.close()
    assert found is not None and found.id == book.id


async def test_find_by_hash_miss_returns_none() -> None:
    store, database = await _new_store()
    try:
        assert await store.find_by_hash("x" * 64) is None
    finally:
        await database.conn.close()
