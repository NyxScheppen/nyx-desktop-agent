import io
import threading

import pytest
from PIL import Image

from nyx.activity.game_observer import (
    RapidOcrEngine,
    build_ocr_observation,
    canonical_observation_hash,
    observation_to_snapshot,
    score_observation,
    validate_game_vision_request,
)
from nyx.activity.screen import validate_bridge_frame
from nyx.enums import (
    EvidenceSource,
    GamePhase,
    GameProfile,
    TextSource,
)
from nyx.types import (
    AcceptedObservationSnapshot,
    GameChoice,
    GameTextBlock,
    GameVisionRequest,
    ObservationEvidence,
    PixelRect,
    WindowCandidate,
    WindowTarget,
)


def _block(text: str = "Why?") -> GameTextBlock:
    return GameTextBlock(
        "ocr:1", text, (1, 1, 100, 30), 0, 0.95,
        [0.95] * len(text), TextSource.OCR, ["ocr:1"],
    )


def _snapshot() -> AcceptedObservationSnapshot:
    return AcceptedObservationSnapshot(
        "s", "disco_elysium", GameProfile.DISCO_ELYSIUM, 1, 1, 1,
        GamePhase.DIALOGUE, "", 0.0, "Kim Kitsuragi", ["ocr:1"],
        [_block()], [], [GameChoice("2", "Continue", 0, None, 0.9, ["ocr:1"])],
        ["Kim Kitsuragi"], ["crop:1"], "A conversation",
        ["crop:1"], 0.91,
        [ObservationEvidence("ocr:1", EvidenceSource.OCR, "ocr:1", None)], [],
    )


def test_canonical_hash_matches_spec_fixture() -> None:
    snapshot = _snapshot()
    got = canonical_observation_hash(snapshot)
    assert got == (
        "sha256:a383799494a856dad522e83ce523aa727f4943bbfc38153c6d5b34d92c464761"
    )


def test_score_observation_uses_four_components() -> None:
    score, components = score_observation(0.8, 0.9, 1.0, 0.5)
    assert score == pytest.approx(0.805)
    assert set(components) == {
        "ocr_confidence", "evidence_coverage", "temporal_agreement", "source_agreement"
    }


def test_vision_request_rejects_ocr_budget() -> None:
    request = GameVisionRequest(
        "s", GameProfile.GENERIC_TEXT, 1, True, [],
        [_block("x" * 513)], None,
    )
    with pytest.raises(ValueError, match="ocr"):
        validate_game_vision_request(request)


def test_validate_bridge_frame_checks_png_and_identity() -> None:
    out = io.BytesIO()
    Image.new("RGB", (20, 10), "white").save(out, format="PNG")
    candidate = WindowCandidate(
        "hwnd:0x10", 16, 2, "game.exe", None, 3, "Game",
        PixelRect(0, 0, 20, 10), 1.0, True, False,
    )
    target = WindowTarget(
        candidate.window_id, candidate.hwnd, candidate.pid, candidate.process_name,
        candidate.process_path, candidate.process_start_time_ms, candidate.title,
        candidate.client_bounds_physical, candidate.scale_factor, candidate.foreground,
        candidate.minimized, GameProfile.GENERIC_TEXT, "g", 1,
    )
    frame = validate_bridge_frame(target, out.getvalue(), "c", 0)
    assert (frame.width, frame.height, frame.window_id) == (20, 10, "hwnd:0x10")


def test_ocr_observation_is_tentative_then_accepted_after_stable_frame() -> None:
    blocks = [
        GameTextBlock(
            "line:1", "Why?", (100, 350, 500, 390), 0, 0.95,
            [0.95] * 4, TextSource.OCR, [],
        ),
        GameTextBlock(
            "line:2", "Continue", (100, 700, 500, 740), 1, 0.92,
            [0.92] * 8, TextSource.OCR, [],
        ),
    ]
    first, first_report = build_ocr_observation(
        session_id="s", game_id="disco_elysium", profile=GameProfile.DISCO_ELYSIUM,
        profile_version=1, threshold_version=1, revision=1, captured_at=1.0,
        width=1920, height=1080, blocks=blocks,
    )
    second, second_report = build_ocr_observation(
        session_id="s", game_id="disco_elysium", profile=GameProfile.DISCO_ELYSIUM,
        profile_version=1, threshold_version=1, revision=1, captured_at=1.5,
        width=1920, height=1080, blocks=blocks, previous=first,
    )
    assert first_report.status.value == "tentative"
    assert second_report.status.value == "accepted"
    assert second.choices[0].text == "Continue"
    snapshot = observation_to_snapshot(second)
    assert snapshot is not None
    assert snapshot.observation_hash.startswith("sha256:")
    assert snapshot.observation_hash == canonical_observation_hash(snapshot)


def test_ocr_observation_rejects_out_of_bounds_text() -> None:
    block = GameTextBlock(
        "line:1", "bad", (-1, 10, 20, 30), 0, 0.9, [0.9] * 3,
        TextSource.OCR, [],
    )
    observation, report = build_ocr_observation(
        session_id="s", game_id="generic", profile=GameProfile.GENERIC_TEXT,
        profile_version=1, threshold_version=1, revision=1, captured_at=1.0,
        width=800, height=450, blocks=[block],
    )
    assert report.status.value == "rejected"
    assert "bbox_out_of_bounds" in report.hard_failures
    assert observation.status.value == "rejected"


@pytest.mark.asyncio
async def test_rapid_ocr_timeout_does_not_start_second_worker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = RapidOcrEngine(timeout_seconds=0.01)
    started = threading.Event()
    release = threading.Event()

    def blocking(_: bytes) -> list[GameTextBlock]:
        started.set()
        release.wait(timeout=2.0)
        return []

    monkeypatch.setattr(engine, "_recognize_sync", blocking)
    first = await engine.recognize(b"frame", 1, 1)
    assert started.is_set()
    second = await engine.recognize(b"frame", 1, 1)
    release.set()
    assert first == ([], "ocr_unavailable")
    assert second == ([], "ocr_busy")
