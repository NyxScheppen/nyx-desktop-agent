# pyright: reportPrivateUsage=false
from __future__ import annotations

import asyncio
import io
from types import SimpleNamespace
from typing import cast

import pytest
from httpx import ASGITransport, AsyncClient
from PIL import Image

from nyx.enums import ActivityStatus, ActivityType, GameProfile, TextSource
from nyx.main import _App, build_app
from nyx.types import AcceptedObservationSnapshot, Activity, GameTextBlock


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

    async def record_game_observation(
        self, session_id: str, snapshot: AcceptedObservationSnapshot
    ) -> str:
        game = self.activity.progress["game_companion"]
        if game.get("last_observation_hash") == snapshot.observation_hash:
            return str(game.get("last_observation_event", "event-1"))
        game["last_accepted_revision"] = snapshot.revision
        game["last_observation_hash"] = snapshot.observation_hash
        game["last_observation_event"] = "event-1"
        from nyx.activity.game_observer import snapshot_to_dict

        game["last_observation"] = snapshot_to_dict(snapshot)
        return "event-1"


class _OversizedActivity(_FakeActivity):
    async def record_game_observation(
        self, session_id: str, snapshot: AcceptedObservationSnapshot
    ) -> str:
        raise ValueError("observation_payload_too_large")


class _FakeOcr:
    async def recognize(
        self, image_bytes: bytes, width: int, height: int
    ) -> tuple[list[GameTextBlock], str | None]:
        return [
            GameTextBlock(
                "line:1", "Continue", (1, 1, 19, 9), 0, 0.9,
                [0.9] * 8, TextSource.OCR, [],
            )
        ], None


class _BlockingOcr:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.calls = 0

    async def recognize(
        self, image_bytes: bytes, width: int, height: int
    ) -> tuple[list[GameTextBlock], str | None]:
        self.calls += 1
        self.started.set()
        await self.release.wait()
        return [], "ocr_unavailable"


def _client(activity: Activity, ocr: object | None = None) -> AsyncClient:
    app = build_app(
        cast(_App, SimpleNamespace(activity=_FakeActivity(activity), game_ocr=ocr))
    )
    return AsyncClient(
        transport=ASGITransport(app=app), base_url="http://127.0.0.1:8000"
    )


def _client_with_activity(activity: object, ocr: object | None = None) -> AsyncClient:
    app = build_app(
        cast(_App, SimpleNamespace(activity=activity, game_ocr=ocr))
    )
    return AsyncClient(
        transport=ASGITransport(app=app), base_url="http://127.0.0.1:8000"
    )


def _png(width: int = 20, height: int = 10) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), "white").save(buffer, format="PNG")
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
    body = response.json()
    assert body["accepted"] is False
    assert body["status"] == "rejected"
    assert body["error_code"] == "ocr_unavailable"


@pytest.mark.asyncio
async def test_game_frame_passes_injected_ocr_through_observation_pipeline() -> None:
    async with _client(_activity(), _FakeOcr()) as client:
        response = await client.post(
            "/api/game-companion/bridge/sessions/session-1/frames",
            content=_png(),
            headers=_headers(),
        )
    assert response.status_code == 200
    body = response.json()
    assert body["accepted"] is False
    assert body["status"] == "tentative"
    assert body["validation"]["score"] > 0.0


@pytest.mark.asyncio
async def test_two_stable_injected_frames_are_committed() -> None:
    activity = _activity()
    async with _client(activity, _FakeOcr()) as client:
        headers = _headers()
        first = await client.post(
            "/api/game-companion/bridge/sessions/session-1/frames",
            content=_png(960, 540), headers=headers,
        )
        second = await client.post(
            "/api/game-companion/bridge/sessions/session-1/frames",
            content=_png(960, 540), headers=headers,
        )
    assert first.json()["status"] == "tentative"
    assert second.json()["accepted"] is True
    assert second.json()["status"] == "accepted"
    assert second.json()["revision"] == 1


@pytest.mark.asyncio
async def test_duplicate_accepted_frame_returns_durable_revision() -> None:
    activity = _activity()
    async with _client(activity, _FakeOcr()) as client:
        headers = _headers()
        await client.post(
            "/api/game-companion/bridge/sessions/session-1/frames",
            content=_png(960, 540), headers=headers,
        )
        accepted = await client.post(
            "/api/game-companion/bridge/sessions/session-1/frames",
            content=_png(960, 540), headers=headers,
        )
        duplicate = await client.post(
            "/api/game-companion/bridge/sessions/session-1/frames",
            content=_png(960, 540), headers=_headers(revision=1),
        )
    assert accepted.json()["revision"] == 1
    assert duplicate.status_code == 200
    assert duplicate.json()["accepted"] is True
    assert duplicate.json()["revision"] == 1
    assert duplicate.json()["observation_hash"] == accepted.json()["observation_hash"]
    assert activity.progress["game_companion"]["last_accepted_revision"] == 1


@pytest.mark.asyncio
async def test_game_frame_rejects_second_inflight_frame_for_same_session() -> None:
    ocr = _BlockingOcr()
    async with _client(_activity(), ocr) as client:
        first_task = asyncio.create_task(
            client.post(
                "/api/game-companion/bridge/sessions/session-1/frames",
                content=_png(), headers=_headers(),
            )
        )
        await ocr.started.wait()
        second = await client.post(
            "/api/game-companion/bridge/sessions/session-1/frames",
            content=_png(), headers=_headers(),
        )
        ocr.release.set()
        first = await first_task
    assert second.status_code == 409
    assert second.json()["detail"] == "frame_busy"
    assert first.status_code == 200
    assert ocr.calls == 1


@pytest.mark.asyncio
async def test_observation_payload_limit_maps_to_413() -> None:
    activity = _OversizedActivity(_activity())
    async with _client_with_activity(activity, _FakeOcr()) as client:
        headers = _headers()
        await client.post(
            "/api/game-companion/bridge/sessions/session-1/frames",
            content=_png(960, 540), headers=headers,
        )
        response = await client.post(
            "/api/game-companion/bridge/sessions/session-1/frames",
            content=_png(960, 540), headers=headers,
        )
    assert response.status_code == 413
    assert response.json()["detail"] == "observation_payload_too_large"
