"""ReadingStore 单元测试（19-reading-content）：:memory: + 真 store。

store 是 books/paragraphs 唯一写路径，直接测其契约：`insert_book_with_paragraphs`
单锁单事务原子落书+段、`content_hash` 去重（唯一索引 + IntegrityError 回退）、
并发同哈希只出一本书、`find_by_hash` 命中/落空。
"""

import asyncio
from uuid import uuid4

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


# ---- 20-reading-progress：进度 / 书架 / 分页 ----

class _Clock:
    """可预测递增时钟，验证 `updated_at` 推进（每次调用 +1 秒）。"""

    def __init__(self) -> None:
        self._t = 1000.0

    def __call__(self) -> float:
        self._t += 1.0
        return self._t


async def _seed_book(
    store: ReadingStore, title: str = "书", n: int = 3
) -> str:
    """插入一本含 n 段正文的书（index 1..n，首段 is_chapter_start=True），返回 id。"""
    book, _ = await store.insert_book_with_paragraphs(
        title=title, author="作者", filename=f"{uuid4().hex}.epub",
        content_hash=uuid4().hex,
        segments=[
            Segment(text=f"第{i}段", is_chapter_start=(i == 1))
            for i in range(1, n + 1)
        ],
    )
    return book.id


async def test_find_book_hit_and_miss() -> None:
    store, database = await _new_store()
    try:
        book_id = await _seed_book(store)
        found = await store.find_book(book_id)
        missing = await store.find_book("nope")
    finally:
        await database.conn.close()
    assert found is not None and found.id == book_id
    assert missing is None


async def test_list_books_unread_sentinel_and_read_ordering() -> None:
    store, database = await _new_store()
    try:
        unread_id = await _seed_book(store, title="未读")
        read_id = await _seed_book(store, title="读过")
        await store.upsert_progress(read_id, 3, 2, 50)
        items = await store.list_books()
    finally:
        await database.conn.close()
    assert [i.id for i in items] == [read_id, unread_id]  # 已读排前，未读排后
    by_id = {i.id: i for i in items}
    assert by_id[unread_id].user_position == 0  # 未读哨兵
    assert by_id[unread_id].last_read_at is None
    assert by_id[read_id].user_position == 3
    assert by_id[read_id].last_read_at is not None


async def test_list_paragraphs_range_ascending_and_bool_restored() -> None:
    store, database = await _new_store()
    try:
        book_id = await _seed_book(store, n=5)
        paras = await store.list_paragraphs(book_id, 1, 2)
    finally:
        await database.conn.close()
    assert [p.index for p in paras] == [1, 2]
    assert paras[0].is_chapter_start is True  # INTEGER 1 → bool True
    assert paras[1].is_chapter_start is False


async def test_get_progress_none_then_value() -> None:
    store, database = await _new_store()
    try:
        book_id = await _seed_book(store)
        assert await store.get_progress(book_id) is None
        await store.upsert_progress(book_id, 4, 3, 70)
        got = await store.get_progress(book_id)
    finally:
        await database.conn.close()
    assert got is not None
    assert got.book_id == book_id
    assert got.user_position == 4
    assert got.nyx_position == 3
    assert got.reading_speed == 70
    assert got.read_count == 0  # 新建行默认 0


async def test_upsert_progress_insert_then_update(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("nyx.reading.store.time.time", _Clock())
    store, database = await _new_store()
    try:
        book_id = await _seed_book(store)
        first = await store.upsert_progress(book_id, 2, 2, 50)
        second = await store.upsert_progress(book_id, 5, 5, 80)
        cursor = await database.conn.execute(
            "SELECT COUNT(*) AS n FROM reading_progress WHERE book_id = ?",
            (book_id,),
        )
        row = await cursor.fetchone()
    finally:
        await database.conn.close()
    assert first.user_position == 2
    assert second.user_position == 5
    assert second.reading_speed == 80
    assert row is not None and row["n"] == 1  # 同一 book_id 单行
    assert second.updated_at > first.updated_at  # 更新推进时间戳


async def test_upsert_does_not_reset_read_count() -> None:
    store, database = await _new_store()
    try:
        book_id = await _seed_book(store)
        await store.increment_read_count(book_id)  # read_count = 1
        saved = await store.upsert_progress(book_id, 6, 6, 90)
        cursor = await database.conn.execute(
            "SELECT read_count FROM reading_progress WHERE book_id = ?", (book_id,),
        )
        row = await cursor.fetchone()
    finally:
        await database.conn.close()
    assert saved.user_position == 6
    assert row is not None and row["read_count"] == 1  # 进度写回不重置重读计数


async def test_increment_read_count_zero_to_one_to_two() -> None:
    store, database = await _new_store()
    try:
        book_id = await _seed_book(store)
        first = await store.increment_read_count(book_id)
        second = await store.increment_read_count(book_id)
    finally:
        await database.conn.close()
    assert first.read_count == 1
    assert second.read_count == 2


async def test_increment_read_count_creates_default_row() -> None:
    store, database = await _new_store()
    try:
        book_id = await _seed_book(store)
        result = await store.increment_read_count(book_id)
        cursor = await database.conn.execute(
            "SELECT user_position, nyx_position, reading_speed "
            "FROM reading_progress WHERE book_id = ?", (book_id,),
        )
        row = await cursor.fetchone()
    finally:
        await database.conn.close()
    assert result.read_count == 1
    assert row is not None
    assert row["user_position"] == 1  # DDL DEFAULT
    assert row["nyx_position"] == 1
    assert row["reading_speed"] == 50


async def test_delete_book_cascades_reading_progress() -> None:
    store, database = await _new_store()
    try:
        book_id = await _seed_book(store)
        await store.upsert_progress(book_id, 2, 2, 50)
        await database.conn.execute("DELETE FROM books WHERE id = ?", (book_id,))
        await database.conn.commit()
        cursor = await database.conn.execute(
            "SELECT COUNT(*) AS n FROM reading_progress WHERE book_id = ?", (book_id,),
        )
        row = await cursor.fetchone()
    finally:
        await database.conn.close()
    assert row is not None and row["n"] == 0
