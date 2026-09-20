# pyright: reportPrivateUsage=false
from __future__ import annotations

import io
from types import SimpleNamespace
from typing import cast

import pytest
from httpx import ASGITransport, AsyncClient
from PIL import Image

from nyx.enums import ActivityStatus, ActivityType, GameProfile
from nyx.main import _App, build_app
from nyx.types import Activity


def _activity(*, revision: int = 0, pid: int = 42, start_time: int = 1234) -> Activity:
    return Activity(
        id="activity-1",
        type=ActivityType.GAME_COMPANION,
        schedule_block_id="2026-09-19T11:00",
        status=ActivityStatus.RUNNING,
        progress={
            "game_companion": {
                "session_id": "session-1",
                "game_id": "disco_elysium",
                "profile": GameProfile.DISCO_ELYSIUM.value,
                "profile_version": 1,
                "status": "observing",
                "last_accepted_revision": revision,
                "window_identity": {
                    "window_id": "hwnd:0x10",
                    "hwnd": 16,
                    "pid": pid,
                    "process_name": "game.exe",
                    "process_start_time_ms": start_time,
                },
            }
        },
        started_at=0.0,
    )


class _FakeActivity:
    def __init__(self, activity: Activity) -> None:
        self.activity = activity

    async def get_game_session(self, session_id: str) -> Activity | None:
        if session_id != "session-1":
            return None
        return self.activity


def _client(activity: Activity) -> AsyncClient:
    app = build_app(cast(_App, SimpleNamespace(activity=_FakeActivity(activity))))
    return AsyncClient(
        transport=ASGITransport(app=app), base_url="http://127.0.0.1:8000"
    )


def _png() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (20, 10), "white").save(buffer, format="PNG")
    return buffer.getvalue()


def _headers(
    *, pid: int = 42, start_time: int = 1234, revision: int = 0
) -> dict[str, str]:
    return {
        "content-type": "image/png",
        "x-nyx-capture-id": "capture-1",
        "x-nyx-window-id": "hwnd:0x10",
        "x-nyx-window-pid": str(pid),
        "x-nyx-window-start-time": str(start_time),
        "x-nyx-expected-revision": str(revision),
    }


@pytest.mark.asyncio
async def test_game_frame_requires_full_window_identity() -> None:
    async with _client(_activity()) as client:
        response = await client.post(
            "/api/game-companion/bridge/sessions/session-1/frames",
            content=_png(),
            headers={
                key: value
                for key, value in _headers().items()
                if key != "x-nyx-window-pid"
            },
        )
    assert response.status_code == 422
    assert response.json()["detail"] == "invalid_payload"


@pytest.mark.asyncio
async def test_game_frame_rejects_stale_revision_before_body_processing() -> None:
    async with _client(_activity(revision=2)) as client:
        response = await client.post(
            "/api/game-companion/bridge/sessions/session-1/frames",
            content=b"not-a-png",
            headers=_headers(revision=1),
        )
    assert response.status_code == 409
    assert response.json()["detail"] == "stale_observation"


@pytest.mark.asyncio
async def test_game_frame_rejects_reused_pid_or_process_start_time() -> None:
    async with _client(_activity()) as client:
        response = await client.post(
            "/api/game-companion/bridge/sessions/session-1/frames",
            content=_png(),
            headers=_headers(pid=99),
        )
    assert response.status_code == 409
    assert response.json()["detail"] == "window_identity_mismatch"


@pytest.mark.asyncio
async def test_game_frame_accepts_matching_identity_and_revision() -> None:
    async with _client(_activity()) as client:
        response = await client.post(
            "/api/game-companion/bridge/sessions/session-1/frames",
            content=_png(),
            headers=_headers(),
        )
    assert response.status_code == 200
    assert response.json()["status"] == "tentative"
