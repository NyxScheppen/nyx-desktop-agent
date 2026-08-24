import pytest

from nyx.tools import web_search


class _FakeDDGS:
    def __init__(self, timeout: int | None = 10) -> None:
        self.timeout = timeout

    def __enter__(self) -> "_FakeDDGS":
        return self

    def __exit__(
        self, exc_type: object, exc_value: object, traceback: object
    ) -> None:
        return None

    def text(self, query: str, max_results: int = 5) -> list[dict[str, str]]:
        return [
            {"title": "T1", "href": "https://a", "body": "B1"},
            {"title": "T2", "href": "https://b", "body": "B2"},
        ]


class _RaisingDDGS(_FakeDDGS):
    def text(self, query: str, max_results: int = 5) -> list[dict[str, str]]:
        raise RuntimeError("network down")


async def test_web_search_maps_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(web_search, "DDGS", _FakeDDGS)
    tool = web_search.build_web_search_tool()
    results = await tool.handler("hello")
    assert results == [
        {"title": "T1", "url": "https://a", "snippet": "B1"},
        {"title": "T2", "url": "https://b", "snippet": "B2"},
    ]


async def test_web_search_returns_empty_on_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(web_search, "DDGS", _RaisingDDGS)
    tool = web_search.build_web_search_tool()
    assert await tool.handler("hello") == []
