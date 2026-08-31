"""POST /api/books 契约测试（19-reading-content）：fake ReadingFacade。

验证端点薄封装：multipart `file` → 201 Book；重复 409；非 .epub/超限/空正文
400；解析失败 500。不碰真实 EPUB/DB。
"""

# pyright: reportPrivateUsage=false
import logging
from typing import cast

import pytest
from httpx import ASGITransport, AsyncClient

from nyx.activity.facade import ActivityFacade
from nyx.config import Config
from nyx.desire.facade import DesireFacade
from nyx.enums import BoundaryResult, ReadingBehavior
from nyx.eval.evaluator import Evaluator
from nyx.events.bus import EventBus
from nyx.expression.facade import ExpressionFacade
from nyx.inner_life.facade import InnerLifeFacade
from nyx.main import _App, build_app
from nyx.memory.facade import MemoryFacade
from nyx.reading.facade import (
    BookNotFoundError,
    DuplicateBookError,
    NoteNotFoundError,
    ReadingFacade,
)
from nyx.types import (
    Annotation,
    Book,
    BookListItem,
    Paragraph,
    ReadingProgress,
    UserNote,
)


class _FakeReading:
    def __init__(self) -> None:
        self.calls: list[tuple[str, bytes]] = []
        self.result: Book | None = None
        self.error: Exception | None = None
        # 20-reading-progress 扩展
        self.books_result: list[BookListItem] = []
        self.books_error: Exception | None = None
        self.progress_result: ReadingProgress | None = None
        self.progress_error: Exception | None = None
        self.saved: list[tuple[str, int, int, int]] = []
        self.save_error: Exception | None = None
        self.paragraphs_result: list[Paragraph] = []
        self.paragraphs_error: Exception | None = None
        # 21-reading-impulse 扩展
        self.impulse_result: list[ReadingBehavior] = []
        self.impulse_calls: list[tuple[str, int, int]] = []
        # 22-reading-notes 扩展
        self.notes_result: list[UserNote] = []
        self.note_result: UserNote | None = None
        self.annotation_result: Annotation | None = None
        self.note_error: Exception | None = None
        self.added_notes: list[tuple[str, str | None, str, str | None]] = []
        self.boundary_result: BoundaryResult = BoundaryResult.NONE

    async def import_book(self, filename: str, data: bytes) -> Book:
        self.calls.append((filename, data))
        if self.error is not None:
            raise self.error
        assert self.result is not None
        return self.result

    async def list_books(self) -> list[BookListItem]:
        if self.books_error is not None:
            raise self.books_error
        return self.books_result

    async def get_progress(self, book_id: str) -> ReadingProgress:
        if self.progress_error is not None:
            raise self.progress_error
        assert self.progress_result is not None
        return self.progress_result

    async def save_progress(
        self, book_id: str, user_position: int, nyx_position: int, reading_speed: int
    ) -> ReadingProgress:
        self.saved.append((book_id, user_position, nyx_position, reading_speed))
        if self.save_error is not None:
            raise self.save_error
        assert self.progress_result is not None
        return self.progress_result

    async def list_paragraphs(
        self, book_id: str, from_idx: int, to_idx: int
    ) -> list[Paragraph]:
        if self.paragraphs_error is not None:
            raise self.paragraphs_error
        return self.paragraphs_result

    async def evaluate_paragraph(
        self, book_id: str, paragraph_index: int, last_paragraph_index: int
    ) -> list[ReadingBehavior]:
        self.impulse_calls.append((book_id, paragraph_index, last_paragraph_index))
        return list(self.impulse_result)

    async def list_user_notes(self, book_id: str) -> list[UserNote]:
        return self.notes_result

    async def add_user_note(
        self,
        book_id: str,
        paragraph_id: str | None,
        content: str,
        selected_text: str | None,
    ) -> UserNote:
        self.added_notes.append((book_id, paragraph_id, content, selected_text))
        assert self.note_result is not None
        return self.note_result

    async def update_user_note(self, note_id: str, content: str) -> UserNote:
        if self.note_error is not None:
            raise self.note_error
        assert self.note_result is not None
        return self.note_result

    async def delete_user_note(self, note_id: str) -> None:
        if self.note_error is not None:
            raise self.note_error

    async def show_to_nyx(self, note_id: str) -> Annotation:
        if self.note_error is not None:
            raise self.note_error
        assert self.annotation_result is not None
        return self.annotation_result

    async def check_chapter_boundary(
        self, book_id: str, nyx_position: int
    ) -> BoundaryResult:
        if self.note_error is not None:
            raise self.note_error
        return self.boundary_result


def _book() -> Book:
    return Book(
        id="b1", title="测试书", author="作者", filename="book.epub",
        content_hash="c" * 64, total_paragraphs=3,
        created_at=1.0, updated_at=1.0,
    )


def _app(reading: _FakeReading) -> _App:
    return _App(
        bus=cast(EventBus, object()),
        inner_life=cast(InnerLifeFacade, object()),
        desire=cast(DesireFacade, object()),
        memory=cast(MemoryFacade, object()),
        activity=cast(ActivityFacade, object()),
        expression=cast(ExpressionFacade, object()),
        reading=cast(ReadingFacade, reading),
        evaluator=cast(Evaluator, object()),
        config=Config(),
    )


def _client(app: _App) -> AsyncClient:
    return AsyncClient(
        transport=ASGITransport(app=build_app(app)), base_url="http://test"
    )


def _note() -> UserNote:
    return UserNote(
        id="n1", book_id="b1", paragraph_id="p1", content="这段写得真好",
        selected_text="真好", created_at=1.0, updated_at=2.0,
    )


def _annotation() -> Annotation:
    return Annotation(
        id="a1", user_note_id="n1", content="我也喜欢这段", created_at=3.0
    )


async def test_books_success_returns_201() -> None:
    fake = _FakeReading()
    fake.result = _book()
    async with _client(_app(fake)) as client:
        resp = await client.post(
            "/api/books",
            files={"file": ("book.epub", b"epub-bytes", "application/epub+zip")},
        )
    assert resp.status_code == 201
    body = resp.json()
    assert body["id"] == "b1"
    assert body["title"] == "测试书"
    assert body["total_paragraphs"] == 3
    assert fake.calls == [("book.epub", b"epub-bytes")]


async def test_books_duplicate_returns_409() -> None:
    fake = _FakeReading()
    fake.error = DuplicateBookError("existing-id", "书名")
    async with _client(_app(fake)) as client:
        resp = await client.post(
            "/api/books", files={"file": ("book.epub", b"x", "application/epub+zip")}
        )
    assert resp.status_code == 409
    assert resp.json()["detail"] == {"existing_book_id": "existing-id", "title": "书名"}


async def test_books_non_epub_returns_400() -> None:
    fake = _FakeReading()
    async with _client(_app(fake)) as client:
        resp = await client.post(
            "/api/books", files={"file": ("book.txt", b"x", "text/plain")}
        )
    assert resp.status_code == 400
    assert fake.calls == []


async def test_books_empty_body_returns_400() -> None:
    fake = _FakeReading()
    fake.error = ValueError("EPUB 无正文")
    async with _client(_app(fake)) as client:
        resp = await client.post(
            "/api/books", files={"file": ("book.epub", b"x", "application/epub+zip")}
        )
    assert resp.status_code == 400


async def test_books_parse_failure_returns_500() -> None:
    fake = _FakeReading()
    fake.error = RuntimeError("DRM 保护")
    async with _client(_app(fake)) as client:
        resp = await client.post(
            "/api/books", files={"file": ("book.epub", b"x", "application/epub+zip")}
        )
    assert resp.status_code == 500


async def test_books_parse_failure_logs_error(
    caplog: pytest.LogCaptureFixture,
) -> None:
    fake = _FakeReading()
    fake.error = RuntimeError("DRM 保护")
    with caplog.at_level(logging.ERROR):
        async with _client(_app(fake)) as client:
            resp = await client.post(
                "/api/books",
                files={"file": ("book.epub", b"x", "application/epub+zip")},
            )
    assert resp.status_code == 500
    assert any("导入 EPUB" in record.message for record in caplog.records)


async def test_books_sanitizes_filename() -> None:
    fake = _FakeReading()
    fake.result = _book()
    async with _client(_app(fake)) as client:
        resp = await client.post(
            "/api/books",
            files={"file": ("../../evil<1>.epub", b"x", "application/epub+zip")},
        )
    assert resp.status_code == 201
    assert fake.calls == [("evil1.epub", b"x")]  # 剥路径 + 去 HTML 危险字符


async def test_books_too_large_returns_400(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("nyx.main._MAX_EPUB_BYTES", 5)
    fake = _FakeReading()
    async with _client(_app(fake)) as client:
        resp = await client.post(
            "/api/books",
            files={"file": ("book.epub", b"0123456789", "application/epub+zip")},
        )
    assert resp.status_code == 400
    assert fake.calls == []  # 超限中断，不继续读、不调 import_book


# ---- 20-reading-progress：书架 / 进度 / 段落端点 ----

async def test_books_list_returns_list() -> None:
    fake = _FakeReading()
    fake.books_result = [
        BookListItem(id="b1", title="已读", author="a", filename="f1.epub",
                     total_paragraphs=3, user_position=5, last_read_at=2.0),
        BookListItem(id="b2", title="未读", author="a", filename="f2.epub",
                     total_paragraphs=1, user_position=0, last_read_at=None),
    ]
    async with _client(_app(fake)) as client:
        resp = await client.get("/api/books")
    assert resp.status_code == 200
    body = resp.json()
    assert [b["id"] for b in body] == ["b1", "b2"]
    assert body[0]["user_position"] == 5
    assert body[1]["last_read_at"] is None


async def test_progress_get_returns_value() -> None:
    fake = _FakeReading()
    fake.progress_result = ReadingProgress("b1", 3, 2, 60, 1, 123.0)
    async with _client(_app(fake)) as client:
        resp = await client.get("/api/progress/b1")
    assert resp.status_code == 200
    body = resp.json()
    assert body["user_position"] == 3
    assert body["nyx_position"] == 2
    assert body["reading_speed"] == 60
    assert body["read_count"] == 1


async def test_progress_get_book_not_found_returns_404() -> None:
    fake = _FakeReading()
    fake.progress_error = BookNotFoundError("missing")
    async with _client(_app(fake)) as client:
        resp = await client.get("/api/progress/missing")
    assert resp.status_code == 404


async def test_progress_put_saves_and_returns_ok() -> None:
    fake = _FakeReading()
    fake.progress_result = ReadingProgress("b1", 4, 3, 70, 0, 1.0)
    async with _client(_app(fake)) as client:
        resp = await client.put(
            "/api/progress/b1",
            json={"user_position": 4, "nyx_position": 3, "reading_speed": 70},
        )
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
    assert fake.saved == [("b1", 4, 3, 70)]


async def test_progress_put_missing_reading_speed_returns_422() -> None:
    fake = _FakeReading()
    async with _client(_app(fake)) as client:
        resp = await client.put(
            "/api/progress/b1",
            json={"user_position": 4, "nyx_position": 3},
        )
    assert resp.status_code == 422
    assert fake.saved == []


async def test_progress_put_reading_speed_out_of_range_returns_422() -> None:
    fake = _FakeReading()
    async with _client(_app(fake)) as client:
        resp_low = await client.put(
            "/api/progress/b1",
            json={"user_position": 4, "nyx_position": 3, "reading_speed": 9},
        )
        resp_high = await client.put(
            "/api/progress/b1",
            json={"user_position": 4, "nyx_position": 3, "reading_speed": 201},
        )
    assert resp_low.status_code == 422
    assert resp_high.status_code == 422
    assert fake.saved == []


async def test_progress_put_book_not_found_returns_404() -> None:
    fake = _FakeReading()
    fake.save_error = BookNotFoundError("missing")
    async with _client(_app(fake)) as client:
        resp = await client.put(
            "/api/progress/missing",
            json={"user_position": 1, "nyx_position": 1, "reading_speed": 50},
        )
    assert resp.status_code == 404


async def test_paragraphs_returns_range() -> None:
    fake = _FakeReading()
    fake.paragraphs_result = [
        Paragraph(
            id="p2", book_id="b1", index=2, text="第二段", is_chapter_start=False
        ),
        Paragraph(
            id="p3", book_id="b1", index=3, text="第三段", is_chapter_start=False
        ),
    ]
    async with _client(_app(fake)) as client:
        resp = await client.get("/api/books/b1/paragraphs?from=2&to=3")
    assert resp.status_code == 200
    body = resp.json()
    assert [p["index"] for p in body] == [2, 3]
    assert body[0]["is_chapter_start"] is False


async def test_paragraphs_missing_from_to_returns_422() -> None:
    fake = _FakeReading()
    async with _client(_app(fake)) as client:
        resp = await client.get("/api/books/b1/paragraphs")
    assert resp.status_code == 422


async def test_paragraphs_invalid_range_returns_422() -> None:
    fake = _FakeReading()
    async with _client(_app(fake)) as client:
        resp_from_lt_1 = await client.get("/api/books/b1/paragraphs?from=0&to=2")
        resp_to_lt_from = await client.get("/api/books/b1/paragraphs?from=3&to=2")
    assert resp_from_lt_1.status_code == 422
    assert resp_to_lt_from.status_code == 422


async def test_paragraphs_book_not_found_returns_404() -> None:
    fake = _FakeReading()
    fake.paragraphs_error = BookNotFoundError("missing")
    async with _client(_app(fake)) as client:
        resp = await client.get("/api/books/missing/paragraphs?from=1&to=2")
    assert resp.status_code == 404


async def test_paragraphs_to_exceeds_total_returns_422() -> None:
    fake = _FakeReading()
    fake.paragraphs_error = ValueError("段落越界")
    async with _client(_app(fake)) as client:
        resp = await client.get("/api/books/b1/paragraphs?from=1&to=99")
    assert resp.status_code == 422


# ---- 21-reading-impulse：冲动端点 ----

async def test_impulse_evaluate_returns_triggered() -> None:
    fake = _FakeReading()
    fake.impulse_result = [
        ReadingBehavior.ASSOCIATE, ReadingBehavior.QUESTION_KNOWLEDGE,
    ]
    async with _client(_app(fake)) as client:
        resp = await client.post(
            "/api/impulse/evaluate",
            json={"book_id": "b1", "paragraph_index": 2, "last_paragraph_index": 1},
        )
    assert resp.status_code == 200
    assert resp.json() == {"triggered": ["associate", "question_knowledge"]}
    assert fake.impulse_calls == [("b1", 2, 1)]


async def test_impulse_evaluate_missing_last_paragraph_returns_422() -> None:
    fake = _FakeReading()
    async with _client(_app(fake)) as client:
        resp = await client.post(
            "/api/impulse/evaluate",
            json={"book_id": "b1", "paragraph_index": 2},
        )
    assert resp.status_code == 422
    assert fake.impulse_calls == []


# ---- 22-reading-notes：笔记 / 批注 / 章节边界端点 ----

async def test_notes_list_returns_list() -> None:
    fake = _FakeReading()
    fake.notes_result = [_note()]
    async with _client(_app(fake)) as client:
        resp = await client.get("/api/notes/b1")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["id"] == "n1"
    assert body[0]["content"] == "这段写得真好"
    assert body[0]["selected_text"] == "真好"


async def test_notes_add_returns_201() -> None:
    fake = _FakeReading()
    fake.note_result = _note()
    async with _client(_app(fake)) as client:
        resp = await client.post(
            "/api/notes/user",
            json={"book_id": "b1", "paragraph_id": "p1",
                  "content": "这段写得真好", "selected_text": "真好"},
        )
    assert resp.status_code == 201
    assert resp.json()["id"] == "n1"
    assert fake.added_notes == [("b1", "p1", "这段写得真好", "真好")]


async def test_notes_add_missing_content_returns_422() -> None:
    fake = _FakeReading()
    async with _client(_app(fake)) as client:
        resp = await client.post(
            "/api/notes/user", json={"book_id": "b1"},
        )
    assert resp.status_code == 422
    assert fake.added_notes == []


async def test_notes_add_optional_fields_default_none() -> None:
    fake = _FakeReading()
    fake.note_result = _note()
    async with _client(_app(fake)) as client:
        resp = await client.post(
            "/api/notes/user", json={"book_id": "b1", "content": "自由笔记"},
        )
    assert resp.status_code == 201
    assert fake.added_notes == [("b1", None, "自由笔记", None)]


async def test_notes_update_returns_note() -> None:
    fake = _FakeReading()
    fake.note_result = _note()
    async with _client(_app(fake)) as client:
        resp = await client.put(
            "/api/notes/user/n1", json={"content": "改过的内容"},
        )
    assert resp.status_code == 200
    assert resp.json()["id"] == "n1"


async def test_notes_update_not_found_returns_404() -> None:
    fake = _FakeReading()
    fake.note_error = NoteNotFoundError("missing")
    async with _client(_app(fake)) as client:
        resp = await client.put(
            "/api/notes/user/missing", json={"content": "x"},
        )
    assert resp.status_code == 404


async def test_notes_delete_returns_204() -> None:
    fake = _FakeReading()
    async with _client(_app(fake)) as client:
        resp = await client.delete("/api/notes/user/n1")
    assert resp.status_code == 204


async def test_notes_delete_not_found_returns_404() -> None:
    fake = _FakeReading()
    fake.note_error = NoteNotFoundError("missing")
    async with _client(_app(fake)) as client:
        resp = await client.delete("/api/notes/user/missing")
    assert resp.status_code == 404


async def test_notes_show_to_nyx_returns_annotation() -> None:
    fake = _FakeReading()
    fake.annotation_result = _annotation()
    async with _client(_app(fake)) as client:
        resp = await client.post("/api/notes/n1/show-to-nyx")
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == "a1"
    assert body["user_note_id"] == "n1"
    assert body["content"] == "我也喜欢这段"


async def test_notes_show_to_nyx_not_found_returns_404() -> None:
    fake = _FakeReading()
    fake.note_error = NoteNotFoundError("missing")
    async with _client(_app(fake)) as client:
        resp = await client.post("/api/notes/missing/show-to-nyx")
    assert resp.status_code == 404


async def test_boundary_chapter_end() -> None:
    fake = _FakeReading()
    fake.boundary_result = BoundaryResult.CHAPTER_END
    async with _client(_app(fake)) as client:
        resp = await client.post(
            "/api/notes/check-chapter-boundary",
            json={"book_id": "b1", "nyx_position": 5},
        )
    assert resp.status_code == 200
    assert resp.json() == {"is_boundary": True, "book_finished": False}


async def test_boundary_book_finished() -> None:
    fake = _FakeReading()
    fake.boundary_result = BoundaryResult.BOOK_FINISHED
    async with _client(_app(fake)) as client:
        resp = await client.post(
            "/api/notes/check-chapter-boundary",
            json={"book_id": "b1", "nyx_position": 3},
        )
    assert resp.status_code == 200
    assert resp.json() == {"is_boundary": False, "book_finished": True}


async def test_boundary_none() -> None:
    fake = _FakeReading()
    fake.boundary_result = BoundaryResult.NONE
    async with _client(_app(fake)) as client:
        resp = await client.post(
            "/api/notes/check-chapter-boundary",
            json={"book_id": "b1", "nyx_position": 2},
        )
    assert resp.status_code == 200
    assert resp.json() == {"is_boundary": False, "book_finished": False}


async def test_boundary_book_not_found_returns_404() -> None:
    fake = _FakeReading()
    fake.note_error = BookNotFoundError("missing")
    async with _client(_app(fake)) as client:
        resp = await client.post(
            "/api/notes/check-chapter-boundary",
            json={"book_id": "missing", "nyx_position": 1},
        )
    assert resp.status_code == 404


async def test_boundary_missing_nyx_position_returns_422() -> None:
    fake = _FakeReading()
    async with _client(_app(fake)) as client:
        resp = await client.post(
            "/api/notes/check-chapter-boundary", json={"book_id": "b1"},
        )
    assert resp.status_code == 422
