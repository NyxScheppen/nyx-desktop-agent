# pyright: reportPrivateUsage=false
import asyncio
from dataclasses import replace

import pytest

from nyx import db
from nyx.activity.facade import ActivityFacade
from nyx.activity.store import ActivityStore
from nyx.config import ActivityConfig
from nyx.enums import (
    CorrectionField,
    EventType,
    EvidenceSource,
    GamePhase,
    GameProfile,
    TextSource,
)
from nyx.events.bus import EventBus
from nyx.types import (
    AcceptedObservationSnapshot,
    GameChoice,
    GameTextBlock,
    ObservationEvidence,
    WindowIdentity,
)


def _snapshot(
    session_id: str, revision: int, text: str = "Why?"
) -> AcceptedObservationSnapshot:
    block = GameTextBlock(
        "ocr:1", text, (1, 1, 100, 30), 0, 0.95, [0.95] * len(text),
        TextSource.OCR, ["ocr:1"],
    )
    base = AcceptedObservationSnapshot(
        session_id, "disco_elysium", GameProfile.DISCO_ELYSIUM, 1, 1, revision,
        GamePhase.DIALOGUE, "", 0.0, "Kim", ["ocr:1"], [block], [],
        [GameChoice("2", "Continue", 0, None, 0.9, ["ocr:1"])], [], [], None, [],
        0.9, [ObservationEvidence("ocr:1", EvidenceSource.OCR, "ocr:1", None)], [],
    )
    from nyx.activity.game_observer import canonical_observation_hash
    return replace(base, observation_hash=canonical_observation_hash(base))


async def _facade() -> tuple[ActivityFacade, db.Database]:
    database = await db.connect(":memory:")
    store = ActivityStore(database)
    facade = object.__new__(ActivityFacade)
    facade._store = store
    facade._bus = EventBus(database)
    facade._config = ActivityConfig()
    return facade, database


def _identity() -> WindowIdentity:
    return WindowIdentity("hwnd:0x10", 16, 42, "game.exe", 1234)


@pytest.mark.asyncio
async def test_game_session_observation_and_choice_are_durable() -> None:
    facade, database = await _facade()
    try:
        activity = await facade.start_game_companion(
            GameProfile.DISCO_ELYSIUM, "disco_elysium", "hwnd:0x10",
            window_identity=_identity(),
        )
        session_id = activity.progress["game_companion"]["session_id"]
        snapshot = _snapshot(session_id, 1)
        event_id = await facade.record_game_observation(session_id, snapshot)
        assert event_id is not None
        result = await facade.confirm_game_choice(session_id, 1, "2")
        assert result.event_id
        events = await facade._bus.list_events_for_correlation(
            session_id,
            (
                EventType.GAME_SESSION_STARTED,
                EventType.GAME_OBSERVATION,
                EventType.GAME_CHOICE_CONFIRMED,
            ),
            100,
        )
        assert {event.type.value for event in events} >= {
            "game_session_started", "game_observation", "game_choice_confirmed"
        }
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_game_correction_rejects_wrong_shape() -> None:
    facade, database = await _facade()
    try:
        activity = await facade.start_game_companion(
            GameProfile.DISCO_ELYSIUM, "disco_elysium", "hwnd:0x10",
            window_identity=_identity(),
        )
        session_id = activity.progress["game_companion"]["session_id"]
        await facade.record_game_observation(session_id, _snapshot(session_id, 1))
        with pytest.raises(ValueError, match="invalid_correction_value"):
            await facade.correct_game_observation(
                session_id, 1, "c1", CorrectionField.SPEAKER,
                {"text": "Kim", "extra": "no"}, "fix",
            )
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_global_vision_disable_overrides_session_flag_and_identity_is_saved(
) -> None:
    facade, database = await _facade()
    facade._vision_enabled = False
    try:
        activity = await facade.start_game_companion(
            GameProfile.DISCO_ELYSIUM,
            "disco_elysium",
            "hwnd:0x10",
            True,
            window_identity=_identity(),
        )
        game = activity.progress["game_companion"]
        assert game["remote_vision_enabled"] is False
        assert game["window_identity"]["pid"] == 42
        assert game["window_identity"]["process_start_time_ms"] == 1234
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_concurrent_choice_confirmation_has_one_durable_event() -> None:
    facade, database = await _facade()
    try:
        activity = await facade.start_game_companion(
            GameProfile.DISCO_ELYSIUM, "disco_elysium", "hwnd:0x10",
            window_identity=_identity(),
        )
        session_id = activity.progress["game_companion"]["session_id"]
        await facade.record_game_observation(session_id, _snapshot(session_id, 1))
        results = await asyncio.gather(
            facade.confirm_game_choice(session_id, 1, "2"),
            facade.confirm_game_choice(session_id, 1, "2"),
            return_exceptions=True,
        )
        assert sum(not isinstance(item, Exception) for item in results) == 1
        events = await facade._bus.list_events_for_correlation(
            session_id, (EventType.GAME_CHOICE_CONFIRMED,), 10
        )
        assert len(events) == 1
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_observation_event_index_is_bounded_and_new_revision_clears_choices(
) -> None:
    facade, database = await _facade()
    try:
        activity = await facade.start_game_companion(
            GameProfile.DISCO_ELYSIUM, "disco_elysium", "hwnd:0x10",
            window_identity=_identity(),
        )
        session_id = activity.progress["game_companion"]["session_id"]
        for revision in range(1, 130):
            await facade.record_game_observation(
                session_id, _snapshot(session_id, revision, f"Why {revision}?")
            )
        current = await facade.get_game_session(session_id)
        assert current is not None
        game = current.progress["game_companion"]
        assert len(game["observation_events"]) == 128
        assert game["confirmed_choice_events"] == {}
    finally:
        await database.close()
