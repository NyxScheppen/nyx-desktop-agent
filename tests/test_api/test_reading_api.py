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
from nyx.eval.evaluator import Evaluator
from nyx.events.bus import EventBus
from nyx.expression.facade import ExpressionFacade
from nyx.inner_life.facade import InnerLifeFacade
from nyx.main import _App, build_app
from nyx.memory.facade import MemoryFacade
from nyx.reading.facade import DuplicateBookError, ReadingFacade
from nyx.types import Book


class _FakeReading:
    def __init__(self) -> None:
        self.calls: list[tuple[str, bytes]] = []
        self.result: Book | None = None
        self.error: Exception | None = None

    async def import_book(self, filename: str, data: bytes) -> Book:
        self.calls.append((filename, data))
        if self.error is not None:
            raise self.error
        assert self.result is not None
        return self.result


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
