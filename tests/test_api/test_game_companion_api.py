# pyright: reportPrivateUsage=false
from __future__ import annotations

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
        game["last_accepted_revision"] = snapshot.revision
        game["last_observation"] = {
            "session_id": snapshot.session_id,
            "game_id": snapshot.game_id,
            "profile": snapshot.profile.value,
            "profile_version": snapshot.profile_version,
            "threshold_version": snapshot.threshold_version,
            "revision": snapshot.revision,
            "phase": snapshot.phase.value,
            "observation_hash": snapshot.observation_hash,
            "captured_at": snapshot.captured_at,
            "speaker": snapshot.speaker,
            "speaker_evidence_ids": snapshot.speaker_evidence_ids,
            "dialogue": [],
            "text_blocks": [],
            "choices": [],
            "visible_entities": [],
            "entity_evidence_ids": [],
            "scene_summary": None,
            "scene_evidence_ids": [],
            "confidence": snapshot.confidence,
            "evidence": [],
            "uncertainties": [],
        }
        return "event-1"


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


def _client(activity: Activity, ocr: object | None = None) -> AsyncClient:
    app = build_app(
        cast(_App, SimpleNamespace(activity=_FakeActivity(activity), game_ocr=ocr))
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
