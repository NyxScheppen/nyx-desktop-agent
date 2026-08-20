import json
from enum import StrEnum

from nyx.enums import (
    ActivityStatus,
    ActivityType,
    ContextMode,
    DesireStatus,
    DesireType,
    EmotionCategory,
    EnergyState,
    EventType,
    GoalAction,
    MemoryType,
    SearchMode,
    Source,
    TickType,
)

EXPECTED: dict[type[StrEnum], set[str]] = {
    EventType: {
        "user_message", "user_material", "clock_tick", "observation_state", "speak",
        "ask", "think", "mutter", "initiate_chat", "emotion_update", "reflection",
        "memory_created", "memory_promoted", "desire_generated", "desire_satisfied",
        "desire_expired", "activity_start", "activity_end", "activity_interrupted",
    },
    Source: {"external", "internal"},
    TickType: {
        "schedule_block_start", "desire_eval", "mutter_check", "initiate_chat_check",
    },
    ContextMode: {"fast", "slow"},
    EmotionCategory: {
        "neutral", "happy", "sad", "angry", "worried", "shy", "sleepy", "thinking",
    },
    DesireType: {"interaction", "exploration", "creation", "rest"},
    ActivityType: {
        "reading", "free_exploration", "creation", "observe_user", "idle_reflection",
        "rest",
    },
    MemoryType: {"short_term", "long_term"},
    DesireStatus: {"pending", "active", "satisfied", "expired", "suppressed"},
    ActivityStatus: {"pending", "running", "abandoned", "completed", "incomplete"},
    EnergyState: {"energetic", "okay", "tired", "exhausted", "drained"},
    SearchMode: {"keyword", "vector", "association"},
    GoalAction: {"read", "write", "observe"},
}


def test_all_enums_exhaustive() -> None:
    for enum_cls, expected in EXPECTED.items():
        assert {m.value for m in enum_cls} == expected


def test_naming_convention() -> None:
    for enum_cls in EXPECTED:
        assert all(m.value == m.name.lower() for m in enum_cls)


def test_strenum_json_serializable() -> None:
    assert json.dumps(EventType.USER_MESSAGE) == '"user_message"'
