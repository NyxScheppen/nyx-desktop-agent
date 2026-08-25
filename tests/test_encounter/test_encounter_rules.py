# pyright: reportPrivateUsage=false
from typing import Any

from nyx.encounter.rules import (
    _BLOCK_PROBABILITY,
    _CONSEQUENCES,
    _COOLDOWN_SECONDS,
    _MIN_ENERGY,
    consequence_for,
    ending_for,
    growth_memory,
    growth_milestone_key,
    should_encounter,
)
from nyx.enums import EncounterKind, EventType, OptionTone, Source
from nyx.types import Encounter, EncounterOption, Event


def _event(content: dict[str, Any]) -> Event:
    return Event(
        id="e1", timestamp=0.0, source=Source.INTERNAL,
        type=EventType.ACTIVITY_END, content=content, correlation_id="c1",
    )


def test_constants_sane() -> None:
    assert 0.0 < _BLOCK_PROBABILITY < 1.0
    assert _COOLDOWN_SECONDS > 0.0
    assert _MIN_ENERGY >= 0.0


def test_should_encounter_true() -> None:
    assert should_encounter(True, False, 50.0, 1000.0) is True


def test_should_encounter_boundary_true() -> None:
    assert should_encounter(True, False, 30.0, 900.0) is True  # 恰好达标


def test_should_encounter_offline() -> None:
    assert should_encounter(False, False, 50.0, 1000.0) is False


def test_should_encounter_busy() -> None:
    assert should_encounter(True, True, 50.0, 1000.0) is False


def test_should_encounter_low_energy() -> None:
    assert should_encounter(True, False, 29.9, 1000.0) is False


def test_should_encounter_cooldown() -> None:
    assert should_encounter(True, False, 50.0, 899.0) is False


def test_consequence_for_each_tone_has_keys() -> None:
    for tone in OptionTone:
        c = consequence_for(tone)
        assert set(c) == {"energy_delta", "emotion_shift", "desire_value_add"}


def test_consequence_for_bold_values() -> None:
    c = consequence_for(OptionTone.BOLD)
    assert c["energy_delta"] == -5.0
    assert c["emotion_shift"] == {"d_valence": 0.15, "d_arousal": 0.10}
    assert c["desire_value_add"] == {"type": "exploration", "amount": 0.10}


def test_consequence_for_isolated() -> None:
    # 顶层新 dict：改返回值不回改共享表（choose 会往后果里加 "memory" 键）
    a = consequence_for(OptionTone.BOLD)
    assert a is not _CONSEQUENCES[OptionTone.BOLD]
    a["memory"] = {"content": "x", "summary": "y"}
    assert "memory" not in _CONSEQUENCES[OptionTone.BOLD]


def test_ending_for_each_tone() -> None:
    for tone in OptionTone:
        assert ending_for(tone) != ""


def test_growth_milestone_key_book_finished() -> None:
    assert growth_milestone_key(
        _event({"type": "reading", "result": {"completed": True}})
    ) == "book_finished"


def test_growth_milestone_key_non_reading() -> None:
    assert growth_milestone_key(
        _event({"type": "creation", "result": {"completed": True}})
    ) is None


def test_growth_milestone_key_not_completed() -> None:
    assert growth_milestone_key(
        _event({"type": "reading", "result": {"completed": False}})
    ) is None
    assert growth_milestone_key(_event({"type": "reading"})) is None


def test_growth_memory_contains_choice() -> None:
    enc = Encounter(
        id="x", kind=EncounterKind.GROWTH_MOMENT, text="开场",
        options=[EncounterOption(text="勇敢向前", tone=OptionTone.BOLD)],
        correlation_id="c", started_at=0.0,
    )
    m = growth_memory(enc, enc.options[0])
    assert "勇敢向前" in m["content"]
    assert m["summary"] != ""
