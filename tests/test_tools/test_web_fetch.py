# pyright: reportPrivateUsage=false
import ipaddress
import socket
from typing import Any

import pytest

from nyx.tools import web_fetch


def test_fetch_url_sync_returns_empty_on_http_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _raise(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("network down")

    def _always_safe(_url: str) -> bool:
        return True

    monkeypatch.setattr(web_fetch.httpx, "get", _raise)
    monkeypatch.setattr(web_fetch, "_is_safe_url", _always_safe)
    assert web_fetch._fetch_url_sync("https://example.com/a") == ""


def test_is_public_ip_rejects_internal_ranges() -> None:
    internal = ("127.0.0.1", "10.0.0.1", "192.168.1.1", "169.254.1.1", "::1")
    for raw in internal:
        assert web_fetch._is_public_ip(ipaddress.ip_address(raw)) is False


def test_is_public_ip_accepts_public() -> None:
    public = ("8.8.8.8", "1.1.1.1")
    for raw in public:
        assert web_fetch._is_public_ip(ipaddress.ip_address(raw)) is True


def test_is_safe_url_rejects_non_http_scheme() -> None:
    assert web_fetch._is_safe_url("file:///etc/passwd") is False
    assert web_fetch._is_safe_url("ftp://example.com/a") is False
    assert web_fetch._is_safe_url("javascript:alert(1)") is False


def test_is_safe_url_rejects_private_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _fake_getaddrinfo(*args: Any, **kwargs: Any) -> Any:
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.168.1.1", 0))]

    monkeypatch.setattr(web_fetch.socket, "getaddrinfo", _fake_getaddrinfo)
    assert web_fetch._is_safe_url("http://internal.example.com/") is False


def test_is_safe_url_accepts_public_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _fake_getaddrinfo(*args: Any, **kwargs: Any) -> Any:
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 0))]

    monkeypatch.setattr(web_fetch.socket, "getaddrinfo", _fake_getaddrinfo)
    assert web_fetch._is_safe_url("https://example.com/a") is True


def test_is_safe_url_rejects_unresolvable_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _fake_getaddrinfo(*args: Any, **kwargs: Any) -> Any:
        raise socket.gaierror("no such host")

    monkeypatch.setattr(web_fetch.socket, "getaddrinfo", _fake_getaddrinfo)
    assert web_fetch._is_safe_url("https://nonexistent.invalid/") is False


class _FakeRedirect:
    is_redirect = True
    headers = {"location": "http://127.0.0.1/admin"}


def test_fetch_url_sync_rejects_redirect_to_private(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _fake_getaddrinfo(*args: Any, **_kwargs: Any) -> Any:
        host = args[0]
        ip = "8.8.8.8" if host == "example.com" else "127.0.0.1"
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 0))]

    def _fake_get(*_args: Any, **_kwargs: Any) -> _FakeRedirect:
        return _FakeRedirect()

    monkeypatch.setattr(web_fetch.socket, "getaddrinfo", _fake_getaddrinfo)
    monkeypatch.setattr(web_fetch.httpx, "get", _fake_get)
    assert web_fetch._fetch_url_sync("https://example.com/a") == ""


async def test_build_web_fetch_tool_returns_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _fake_fetch(url: str) -> str:
        return "正文内容"

    monkeypatch.setattr(web_fetch, "fetch_url", _fake_fetch)

    tool = web_fetch.build_web_fetch_tool()
    result = await tool.handler("https://example.com/article")

    assert result["text"] == "正文内容"
    assert result["url"] == "https://example.com/article"


async def test_build_web_fetch_tool_returns_error_when_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _fake_fetch(url: str) -> str:
        return ""

    monkeypatch.setattr(web_fetch, "fetch_url", _fake_fetch)

    tool = web_fetch.build_web_fetch_tool()
    result = await tool.handler("https://example.com/article")

    assert "error" in result
