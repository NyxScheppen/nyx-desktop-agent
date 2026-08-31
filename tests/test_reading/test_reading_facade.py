"""ReadingFacade.import_book 集成测试（19-reading-content）：:memory: + 真 store。

`parse_epub` 用 monkeypatch 注入固定 `EpubResult`（本测试不碰真实 EPUB 字节），
验证 import_book 管道：解析 → 去重 → 落库 → 返回 Book，以及重复/空正文/级联。
"""

import pytest

from nyx import db
from nyx.reading import facade as facade_mod
from nyx.reading.epub import EpubResult
from nyx.reading.facade import DuplicateBookError, ReadingFacade
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
