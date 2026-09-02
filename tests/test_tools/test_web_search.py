# pyright: reportPrivateUsage=false
import pytest

from nyx.tools import web_search

_BING_HTML = """
<html><body>
  <li class="b_algo">
    <h2><a href="https://a.example/x">T1</a></h2>
    <p>B1 摘要</p>
  </li>
  <li class="b_algo">
    <h2><a href="https://b.example/y">T2</a></h2>
    <p>B2 摘要</p>
  </li>
  <li class="b_algo">
    <h2>无链接标题</h2>
    <p>应被跳过</p>
  </li>
</body></html>
"""


class _Resp:
    def __init__(self, text: str) -> None:
        self.text = text

    def raise_for_status(self) -> None:
        return None


def test_parse_bing_extracts_title_url_snippet() -> None:
    assert web_search._parse_bing(_BING_HTML) == [
        {"title": "T1", "url": "https://a.example/x", "snippet": "B1 摘要"},
        {"title": "T2", "url": "https://b.example/y", "snippet": "B2 摘要"},
    ]


def test_parse_bing_empty_html() -> None:
    assert web_search._parse_bing("<html></html>") == []


def test_search_web_sync_returns_empty_on_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _boom(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("network down")

    monkeypatch.setattr(web_search.httpx, "get", _boom)
    assert web_search._search_web_sync("hello") == []


def test_search_web_sync_returns_empty_on_no_results(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _empty(*_args: object, **_kwargs: object) -> _Resp:
        return _Resp("<html></html>")

    monkeypatch.setattr(web_search.httpx, "get", _empty)
    assert web_search._search_web_sync("hello") == []


async def test_handler_wires_through_search_sync(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _fake_search(_query: str) -> list[dict[str, str]]:
        return [{"title": "T", "url": "https://x", "snippet": "S"}]

    monkeypatch.setattr(web_search, "_search_web_sync", _fake_search)
    tool = web_search.build_web_search_tool()
    assert await tool.handler("hello") == [
        {"title": "T", "url": "https://x", "snippet": "S"}
    ]
