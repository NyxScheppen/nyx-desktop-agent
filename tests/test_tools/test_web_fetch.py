# pyright: reportPrivateUsage=false
from typing import Any, cast

import pytest

from nyx.enums import EventType, Source
from nyx.events.bus import EventBus
from nyx.tools import web_fetch
from nyx.types import Event


class _FakeBus:
    def __init__(self) -> None:
        self.published: list[Event] = []

    async def publish(self, event: Event) -> None:
        self.published.append(event)


def test_fetch_url_sync_returns_empty_on_http_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _raise(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("network down")

    monkeypatch.setattr(web_fetch.httpx, "get", _raise)
    assert web_fetch._fetch_url_sync("https://example.com/a") == ""


async def test_build_web_fetch_tool_writes_and_publishes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[tuple[str, str, str | None]] = []

    async def _fake_fetch(url: str) -> str:
        return "正文内容"

    async def _fake_file_io(
        action: str, path: str, content: str | None = None
    ) -> dict[str, Any]:
        captured.append((action, path, content))
        return {"path": "/fake/uploads/example.com.txt"}

    monkeypatch.setattr(web_fetch, "fetch_url", _fake_fetch)
    monkeypatch.setattr(web_fetch, "file_io", _fake_file_io)

    bus = _FakeBus()
    tool = web_fetch.build_web_fetch_tool(cast(EventBus, bus))
    result = await tool.handler("https://example.com/article")

    assert result["filename"].endswith(".txt")
    assert result["total_chars"] == len("正文内容")
    assert captured[0][0] == "write"
    assert captured[0][2] == "正文内容"
    assert len(bus.published) == 1
    evt = bus.published[0]
    assert evt.type == EventType.USER_MATERIAL
    assert evt.source == Source.INTERNAL
    assert set(evt.content) == {"path", "filename", "total_chars"}


async def test_build_web_fetch_tool_returns_error_when_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _fake_fetch(url: str) -> str:
        return ""

    monkeypatch.setattr(web_fetch, "fetch_url", _fake_fetch)

    bus = _FakeBus()
    tool = web_fetch.build_web_fetch_tool(cast(EventBus, bus))
    result = await tool.handler("https://example.com/article")

    assert "error" in result
    assert bus.published == []
