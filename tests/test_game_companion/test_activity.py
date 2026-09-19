# pyright: reportPrivateUsage=false
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
)


def _snapshot(session_id: str, revision: int) -> AcceptedObservationSnapshot:
    block = GameTextBlock(
        "ocr:1", "Why?", (1, 1, 100, 30), 0, 0.95, [0.95] * 4,
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


@pytest.mark.asyncio
async def test_game_session_observation_and_choice_are_durable() -> None:
    facade, database = await _facade()
    try:
        activity = await facade.start_game_companion(
            GameProfile.DISCO_ELYSIUM, "disco_elysium", "hwnd:0x10"
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
            GameProfile.DISCO_ELYSIUM, "disco_elysium", "hwnd:0x10"
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
