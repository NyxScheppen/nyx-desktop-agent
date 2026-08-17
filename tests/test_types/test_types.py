from typing import get_type_hints

from nyx.types import (
    DesireStatus,
    DesireType,
    EvalScores,
    Memory,
    MemoryType,
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
    assert set(get_type_hints(EvalScores)) == {"format", "ooc", "relevance"}
    assert set(get_type_hints(TokenUsageDict)) == {"input", "output"}
