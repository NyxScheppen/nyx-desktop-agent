# pyright: reportPrivateUsage=false
from collections.abc import AsyncGenerator
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi.routing import APIRoute
from httpx import ASGITransport, AsyncClient

from nyx.app_context import _App
from nyx.browsing.companions import BrowsingCompanion
from nyx.browsing.facade import BrowsingFacade
from nyx.browsing.integration import BrowsingIntegration
from nyx.browsing.store import BrowsingStore
from nyx.db import connect
from nyx.main import build_app
from nyx.memory.facade import MemoryFacade


@pytest.fixture
async def client() -> AsyncGenerator[AsyncClient, None]:
    database = await connect(":memory:")
    companion = Mock(spec=BrowsingCompanion)
    companion.dispatch = AsyncMock()
    browsing = BrowsingFacade(
        BrowsingStore(database),
        companion,
        Mock(spec=BrowsingIntegration),
        Mock(spec=MemoryFacade),
        bootstrap_secret="secret",
    )
    app = build_app(cast(_App, SimpleNamespace(browsing=browsing)))
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://127.0.0.1:8000"
        ) as http:
            yield http
    finally:
        await browsing.quiesce()
        await browsing.drain(1.0)
        await database.close()


async def test_bridge_bootstrap_and_capture_are_authenticated(
    client: AsyncClient,
) -> None:
    denied = await client.post("/api/browsing/bridge/sessions", json={})
    assert denied.status_code == 401
    response = await client.post(
        "/api/browsing/bridge/sessions",
        json={},
        headers={"Authorization": "Bearer secret"},
    )
    assert response.status_code in (200, 201)
    data = response.json()
    session = data["session"]["id"]
    headers = {"Authorization": "Bearer " + data["bridge_token"]}
    await client.post(
        f"/api/browsing/bridge/sessions/{session}/navigations",
        json={"navigation_id": "nav"},
        headers=headers,
    )
    captured = await client.post(
        f"/api/browsing/bridge/sessions/{session}/pages",
        json={
            "session_id": session,
            "navigation_id": "nav",
            "capture_seq": 1,
            "raw_url": "https://example.com/article?tracking=1",
            "title": "Title",
            "visible_text": "Text",
            "auth_tainted": False,
            "truncated": False,
        },
        headers=headers,
    )
    assert captured.status_code == 201
    state = (await client.get(f"/api/browsing/sessions/{session}")).json()
    assert state["pages"][0]["url"] == "https://example.com/article"
    assert "content_text" not in state["pages"][0]
    assert state["next_cursor"] is None

    invalid_limit = await client.get(
        f"/api/browsing/sessions/{session}", params={"limit": 0}
    )
    assert invalid_limit.status_code == 422


async def test_remote_origins_host_and_media_are_rejected(client: AsyncClient) -> None:
    response = await client.post(
        "/api/chat", json={"message": "Hi"}, headers={"Origin": "https://evil.example"}
    )
    assert response.status_code == 403
    response = await client.get("/api/state", headers={"Host": "evil.example:8000"})
    assert response.status_code == 400
    response = await client.post(
        "/api/chat", content="message=Hi", headers={"Content-Type": "text/plain"}
    )
    assert response.status_code == 415


async def test_history_cursor_pages_and_invalid_cursor(client: AsyncClient) -> None:
    boot = (await client.post(
        "/api/browsing/bridge/sessions", json={},
        headers={"Authorization": "Bearer secret"},
    )).json()
    session = boot["session"]["id"]
    headers = {"Authorization": "Bearer " + boot["bridge_token"]}
    for index in range(3):
        await client.post(
            f"/api/browsing/bridge/sessions/{session}/navigations",
            json={"navigation_id": str(index)}, headers=headers,
        )
        await client.post(
            f"/api/browsing/bridge/sessions/{session}/pages",
            json={
                "session_id": session, "navigation_id": str(index),
                "capture_seq": 1, "raw_url": f"https://example.com/{index}",
                "title": str(index), "visible_text": "Text",
                "auth_tainted": False, "truncated": False,
            }, headers=headers,
        )
    path = f"/api/browsing/sessions/{session}"
    first = (await client.get(path, params={"limit": 2})).json()
    second = (await client.get(
        path, params={"limit": 2, "cursor": first["next_cursor"]}
    )).json()

    assert [page["title"] for page in first["pages"]] == ["0", "1"]
    assert [page["title"] for page in second["pages"]] == ["2"]
    assert second["next_cursor"] is None
    assert (await client.get(path, params={"cursor": "missing"})).status_code == 422


async def test_trusted_packaged_cors_and_pna(client: AsyncClient) -> None:
    response = await client.options(
        "/api/chat",
        headers={
            "Origin": "http://tauri.localhost",
            "Sec-Fetch-Site": "cross-site",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
            "Access-Control-Request-Private-Network": "true",
        },
    )
    assert response.status_code == 204
    assert response.headers["access-control-allow-origin"] == "http://tauri.localhost"
    assert response.headers["access-control-allow-private-network"] == "true"
    assert response.headers["vary"] == "Origin"
    assert "access-control-allow-credentials" not in response.headers


async def test_bridge_body_limit_is_before_json_parsing(client: AsyncClient) -> None:
    response = await client.post(
        "/api/browsing/bridge/sessions",
        content=b"x" * 2097153,
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 413
    assert response.json()["detail"]["code"] == "request_too_large"


async def test_bridge_stream_limit_also_counts_without_content_length(
    client: AsyncClient,
) -> None:
    async def chunks() -> AsyncGenerator[bytes, None]:
        yield b"x" * 1048576
        yield b"y" * 1048577

    response = await client.post(
        "/api/browsing/bridge/sessions",
        content=chunks(),
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 413


@pytest.mark.parametrize(
    "origin",
    [
        "null",
        "http://127.0.0.1:5173",
        "http://localhost:5173/",
        "https://tauri.localhost",
        "https://evil.example",
    ],
)
async def test_origin_allowlist_is_exact(client: AsyncClient, origin: str) -> None:
    response = await client.options(
        "/api/chat",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "POST",
        },
    )
    assert response.status_code == 403
    assert "access-control-allow-origin" not in response.headers


def test_multipart_routes_are_generated_from_route_declarations() -> None:
    app = build_app(cast(_App, SimpleNamespace()))
    multipart = {
        (method, route.path)
        for route in app.routes
        if isinstance(route, APIRoute)
        and route.body_field
        and getattr(route.body_field.field_info, "media_type", None)
        == "multipart/form-data"
        for method in route.methods
    }
    assert multipart == {("POST", "/api/upload"), ("POST", "/api/books")}
