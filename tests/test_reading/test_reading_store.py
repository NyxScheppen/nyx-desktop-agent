"""ReadingStore 单元测试（19-reading-content）：:memory: + 真 store。

store 是 books/paragraphs 唯一写路径，直接测其契约：id/时间戳生成、
`"index"` 从 1 连续、`is_chapter_start` bool→1/0、`find_by_hash` 命中/落空。
"""

from nyx import db
from nyx.reading.segmenter import Segment
from nyx.reading.store import ReadingStore


async def _new_store() -> tuple[ReadingStore, db.Database]:
    database = await db.connect(":memory:")
    return ReadingStore(database), database


async def test_insert_book_generates_id_and_timestamps() -> None:
    store, database = await _new_store()
    try:
        book = await store.insert_book(
            title="挪威的森林", author="村上春树", filename="nwsdl.epub",
            content_hash="a" * 64, total_paragraphs=3,
        )
    finally:
        await database.conn.close()
    assert book.id != ""
    assert book.title == "挪威的森林"
    assert book.author == "村上春树"
    assert book.filename == "nwsdl.epub"
    assert book.content_hash == "a" * 64
    assert book.total_paragraphs == 3
    assert book.created_at > 0
    assert book.updated_at > 0


async def test_find_by_hash_hit() -> None:
    store, database = await _new_store()
    try:
        book = await store.insert_book(
            title="t", author="a", filename="f", content_hash="h" * 64,
            total_paragraphs=1,
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


async def test_insert_paragraphs_index_from_one_and_chapter_flag() -> None:
    store, database = await _new_store()
    try:
        book = await store.insert_book(
            title="t", author="a", filename="f", content_hash="h" * 64,
            total_paragraphs=3,
        )
        await store.insert_paragraphs(
            book.id,
            [
                Segment(text="第一章\n开头", is_chapter_start=True),
                Segment(text="正文一段", is_chapter_start=False),
                Segment(text="正文二段", is_chapter_start=False),
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
    assert [r["index"] for r in rows] == [1, 2, 3]
    assert [r["is_chapter_start"] for r in rows] == [1, 0, 0]
    assert rows[0]["text"] == "第一章\n开头"
