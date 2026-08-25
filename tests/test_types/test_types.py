from typing import get_type_hints

from nyx.enums import DesireStatus, DesireType, EncounterKind, MemoryType, OptionTone
from nyx.types import (
    Encounter,
    EncounterOption,
    EvalScores,
    LongTermDesire,
    Memory,
    Personality,
    ShortTermDesire,
    TokenUsageDict,
    Values,
)


def test_short_term_desire_default_status() -> None:
    desire = ShortTermDesire("", 0.0, DesireType.INTERACTION, 1.0, "", None)
    assert desire.status is DesireStatus.PENDING


def test_memory_aspect_default_factory_isolated() -> None:
    first = Memory("", 0.0, "", "", "", 1.0, MemoryType.SHORT_TERM)
    second = Memory("", 0.0, "", "", "", 1.0, MemoryType.SHORT_TERM)
    first.aspect.append("x")
    assert second.aspect == []


def test_long_term_desire_linked_values_default_factory_isolated() -> None:
    first = LongTermDesire("", 0.0, DesireType.INTERACTION, "", "", 1.0, 0.0, [])
    second = LongTermDesire("", 0.0, DesireType.INTERACTION, "", "", 1.0, 0.0, [])
    first.linked_values.append("x")
    assert second.linked_values == []


def test_typed_dict_keys() -> None:
    assert set(get_type_hints(Personality)) == {
        "openness",
        "conscientiousness",
        "extraversion",
        "agreeableness",
        "neuroticism",
    }
    assert set(get_type_hints(Values)) == {
        "attitude_to_human",
        "ai_identity_acceptance",
        "altruism",
        "optimism",
    }
    assert set(get_type_hints(EvalScores)) == {"ooc"}
    assert set(get_type_hints(TokenUsageDict)) == {"input", "output"}


def test_encounter_option_fields() -> None:
    opt = EncounterOption(text="走", tone=OptionTone.BOLD)
    assert opt.text == "走"
    assert opt.tone is OptionTone.BOLD


def test_encounter_defaults() -> None:
    enc = Encounter(
        id="e1", kind=EncounterKind.RANDOM_EVENT, text="开场",
        options=[], correlation_id="c1", started_at=0.0,
    )
    assert enc.activity_id is None
    assert enc.chosen_index is None
