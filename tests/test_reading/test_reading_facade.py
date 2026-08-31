"""ReadingFacade.import_book 集成测试（19-reading-content）：:memory: + 真 store。

`parse_epub` 用 monkeypatch 注入固定 `EpubResult`（本测试不碰真实 EPUB 字节），
验证 import_book 管道：解析 → 去重 → 落库 → 返回 Book，以及重复/空正文/级联。
"""

import pytest

from nyx import db
from nyx.reading import facade as facade_mod
from nyx.reading.epub import EpubResult
from nyx.reading.facade import BookNotFoundError, DuplicateBookError, ReadingFacade
from nyx.reading.segmenter import Segment
from nyx.reading.store import ReadingStore


async def _facade(
    monkeypatch: pytest.MonkeyPatch,
    segments: list[Segment],
    *,
    title: str = "测试书",
    author: str = "测试作者",
    content_hash: str = "c" * 64,
) -> tuple[ReadingFacade, db.Database]:
    database = await db.connect(":memory:")

    def fake_parse(data: bytes) -> EpubResult:
        return EpubResult(
            title=title, author=author, segments=segments, content_hash=content_hash
        )

    monkeypatch.setattr(facade_mod, "parse_epub", fake_parse)
    return ReadingFacade(ReadingStore(database)), database


async def test_import_book_inserts_book_and_paragraphs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    facade, database = await _facade(
        monkeypatch,
        [
            Segment(text="第一章\n开头", is_chapter_start=True),
            Segment(text="正文", is_chapter_start=False),
            Segment(text="结尾", is_chapter_start=False),
        ],
    )
    try:
        book = await facade.import_book("nwsdl.epub", b"fake")
        cursor = await database.conn.execute(
            'SELECT "index" FROM paragraphs WHERE book_id = ? ORDER BY "index"',
            (book.id,),
        )
        indexes = [r["index"] for r in await cursor.fetchall()]
    finally:
        await database.conn.close()
    assert book.total_paragraphs == 3
    assert book.title == "测试书"
    assert indexes == [1, 2, 3]


async def test_import_book_duplicate_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    facade, database = await _facade(
        monkeypatch, [Segment(text="正文", is_chapter_start=False)],
        content_hash="d" * 64,
    )
    try:
        first = await facade.import_book("a.epub", b"x")
        with pytest.raises(DuplicateBookError) as exc:
            await facade.import_book("b.epub", b"x")
    finally:
        await database.conn.close()
    assert exc.value.existing_book_id == first.id
    assert exc.value.title == "测试书"


async def test_import_book_empty_segments_raises_value_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    facade, database = await _facade(monkeypatch, [])
    try:
        with pytest.raises(ValueError):
            await facade.import_book("a.epub", b"x")
        cursor = await database.conn.execute("SELECT COUNT(*) AS n FROM books")
        row = await cursor.fetchone()
    finally:
        await database.conn.close()
    assert row is not None and row["n"] == 0


async def test_import_book_title_falls_back_to_filename(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    facade, database = await _facade(
        monkeypatch, [Segment(text="正文", is_chapter_start=False)], title="",
    )
    try:
        book = await facade.import_book("我的书.epub", b"x")
    finally:
        await database.conn.close()
    assert book.title == "我的书.epub"


async def test_delete_book_cascades_paragraphs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    facade, database = await _facade(
        monkeypatch,
        [
            Segment(text="正文", is_chapter_start=False),
            Segment(text="续", is_chapter_start=False),
        ],
    )
    try:
        book = await facade.import_book("a.epub", b"x")
        await database.conn.execute("DELETE FROM books WHERE id = ?", (book.id,))
        await database.conn.commit()
        cursor = await database.conn.execute(
            "SELECT COUNT(*) AS n FROM paragraphs WHERE book_id = ?", (book.id,),
        )
        row = await cursor.fetchone()
    finally:
        await database.conn.close()
    assert row is not None and row["n"] == 0


# ---- 20-reading-progress：进度 / 书架 / 分页 ----

async def test_list_books_lists_imported_book(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    facade, database = await _facade(
        monkeypatch, [Segment(text="正文", is_chapter_start=False)],
    )
    try:
        book = await facade.import_book("a.epub", b"x")
        items = await facade.list_books()
    finally:
        await database.conn.close()
    assert len(items) == 1
    assert items[0].id == book.id
    assert items[0].user_position == 0  # 未读哨兵
    assert items[0].last_read_at is None


async def test_get_progress_default_when_no_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    facade, database = await _facade(
        monkeypatch, [Segment(text="正文", is_chapter_start=False)],
    )
    try:
        book = await facade.import_book("a.epub", b"x")
        progress = await facade.get_progress(book.id)
    finally:
        await database.conn.close()
    assert progress.book_id == book.id
    assert progress.user_position == 1
    assert progress.nyx_position == 1
    assert progress.reading_speed == 50
    assert progress.read_count == 0
    assert progress.updated_at == 0.0  # 从未保存哨兵


async def test_save_progress_insert_then_update(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    facade, database = await _facade(
        monkeypatch, [Segment(text="正文", is_chapter_start=False)],
    )
    try:
        book = await facade.import_book("a.epub", b"x")
        first = await facade.save_progress(book.id, 2, 2, 50)
        second = await facade.save_progress(book.id, 5, 4, 80)
        cursor = await database.conn.execute(
            "SELECT COUNT(*) AS n FROM reading_progress WHERE book_id = ?", (book.id,),
        )
        row = await cursor.fetchone()
    finally:
        await database.conn.close()
    assert first.user_position == 2
    assert second.user_position == 5
    assert second.reading_speed == 80
    assert second.read_count == 0  # save 不写 read_count
    assert row is not None and row["n"] == 1


async def test_list_paragraphs_range(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    facade, database = await _facade(
        monkeypatch,
        [Segment(text=f"第{i}段", is_chapter_start=(i == 1)) for i in range(1, 6)],
    )
    try:
        book = await facade.import_book("a.epub", b"x")
        paras = await facade.list_paragraphs(book.id, 2, 4)
    finally:
        await database.conn.close()
    assert [p.index for p in paras] == [2, 3, 4]
    assert paras[0].is_chapter_start is False


async def test_list_paragraphs_to_idx_exceeds_total_raises_value_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    facade, database = await _facade(
        monkeypatch, [Segment(text="正文", is_chapter_start=False)],
    )
    try:
        book = await facade.import_book("a.epub", b"x")
        with pytest.raises(ValueError):
            await facade.list_paragraphs(book.id, 1, 99)
    finally:
        await database.conn.close()


async def test_book_not_found_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    facade, database = await _facade(
        monkeypatch, [Segment(text="正文", is_chapter_start=False)],
    )
    try:
        with pytest.raises(BookNotFoundError):
            await facade.get_progress("missing")
        with pytest.raises(BookNotFoundError):
            await facade.save_progress("missing", 1, 1, 50)
        with pytest.raises(BookNotFoundError):
            await facade.list_paragraphs("missing", 1, 2)
    finally:
        await database.conn.close()
