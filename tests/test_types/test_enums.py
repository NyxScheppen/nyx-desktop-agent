import json
import re
from enum import StrEnum
from pathlib import Path

from nyx.enums import (
    ActivityStatus,
    ActivityType,
    BoundaryResult,
    ContextMode,
    DesireStatus,
    DesireType,
    EmotionCategory,
    EnergyState,
    EventType,
    GoalAction,
    MemoryEdgeKind,
    MemoryType,
    ReadingBehavior,
    ReadingDrive,
    SearchMode,
    Source,
    TickType,
)

EXPECTED: dict[type[StrEnum], set[str]] = {
    EventType: {
        "user_message", "clock_tick", "observation_state", "speak",
        "ask", "think", "mutter", "initiate_chat", "emotion_update", "reflection",
        "reflection_done", "memory_created", "memory_promoted", "desire_generated",
        "desire_satisfied", "desire_expired", "activity_start", "activity_end",
        "activity_interrupted", "reading_mutter", "reading_question",
        "reading_association",
        "browsing_mutter", "browsing_question", "browsing_association",
    },
    Source: {"external", "internal"},
    TickType: {
        "schedule_block_start", "desire_eval", "mutter_check", "initiate_chat_check",
        "reflection_check",
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
    MemoryEdgeKind: {
        "semantic", "entity", "keyword", "temporal",
        "same_topic", "elaborates", "contrasts", "causes",
        "updates_preference", "user_profile_link",
    },
    ReadingDrive: {
        "motivation", "curiosity", "boredom", "aesthetic_sensitivity",
        "empathy_bias", "associative_drive",
    },
    ReadingBehavior: {
        "question_knowledge", "question_personal", "question_reflective",
        "quote_question", "associate",
    },
    DesireStatus: {"pending", "active", "satisfied", "expired", "suppressed"},
    ActivityStatus: {
        "pending", "running", "paused", "abandoned", "completed", "incomplete",
    },
    EnergyState: {"energetic", "okay", "tired", "exhausted", "drained"},
    SearchMode: {"keyword", "vector", "association"},
    GoalAction: {"read", "write", "observe"},
    BoundaryResult: {"none", "chapter_end", "book_finished"},
}


def test_all_enums_exhaustive() -> None:
    for enum_cls, expected in EXPECTED.items():
        assert {m.value for m in enum_cls} == expected


def test_naming_convention() -> None:
    for enum_cls in EXPECTED:
        assert all(m.value == m.name.lower() for m in enum_cls)


def test_strenum_json_serializable() -> None:
    assert json.dumps(EventType.USER_MESSAGE) == '"user_message"'


def test_frontend_sse_listeners_cover_all_event_types() -> None:
    source = Path("frontend/src/hooks/useSSE.ts").read_text(encoding="utf-8")
    match = re.search(r"const EVENT_TYPES = \[(.*?)\];", source, re.DOTALL)
    assert match is not None
    frontend_types = set(re.findall(r'"([a-z_]+)"', match.group(1)))
    assert frontend_types == {event_type.value for event_type in EventType}
