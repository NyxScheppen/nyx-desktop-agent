from nyx.enums import (
    DesireStatus,
    DesireType,
    MemoryKind,
    MemoryType,
)
from nyx.types import LongTermDesire, Memory, ShortTermDesire, UserNote


def test_short_term_desire_default_status() -> None:
    desire = ShortTermDesire("", 0.0, DesireType.INTERACTION, 1.0, "", None)
    assert desire.status is DesireStatus.PENDING
    assert desire.parent_long_term_id is None


def test_memory_aspect_default_factory_isolated() -> None:
    first = Memory("", 0.0, "", MemoryKind.EPISODE, "", 1.0, MemoryType.SHORT_TERM)
    second = Memory("", 0.0, "", MemoryKind.EPISODE, "", 1.0, MemoryType.SHORT_TERM)
    first.aspect.append("x")
    assert second.aspect == []


def test_user_note_annotations_default_factory_isolated() -> None:
    first = UserNote("", None, None, "", None, 0.0, 0.0)
    second = UserNote("", None, None, "", None, 0.0, 0.0)
    assert first.annotations == []
    assert second.annotations == []
    assert first.annotations is not second.annotations  # 各自独立，非共享单例


def test_long_term_desire_subtopics_are_independent() -> None:
    first = LongTermDesire("", 0.0, DesireType.INTERACTION, "", "", 1.0, 0.0, [])
    second = LongTermDesire("", 0.0, DesireType.INTERACTION, "", "", 1.0, 0.0, [])
    first.subtopics.append("x")
    assert second.subtopics == []
