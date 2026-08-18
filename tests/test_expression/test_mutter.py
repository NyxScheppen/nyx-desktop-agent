# pyright: reportPrivateUsage=false
from nyx.enums import DesireType
from nyx.expression.mutter import _MUTTER_TEMPLATES, pick_mutter, should_initiate_chat
from nyx.types import ShortTermDesire


def _desire(type_: DesireType) -> ShortTermDesire:
    return ShortTermDesire(
        id="d1",
        created_at=0.0,
        type=type_,
        strength=1.0,
        description="想聊聊天",
        goal=None,
    )


def test_templates_len_50_and_unique() -> None:
    assert len(_MUTTER_TEMPLATES) == 50
    assert len(set(_MUTTER_TEMPLATES)) == 50


def test_pick_mutter_out_of_range() -> None:
    assert pick_mutter(-0.1) is None
    assert pick_mutter(1.0) is None


def test_pick_mutter_bounds() -> None:
    assert pick_mutter(0.0) == _MUTTER_TEMPLATES[0]
    assert pick_mutter(0.999) == _MUTTER_TEMPLATES[-1]


def test_pick_mutter_membership() -> None:
    assert pick_mutter(0.37) in _MUTTER_TEMPLATES


def test_should_initiate_chat_all_true() -> None:
    interaction = [_desire(DesireType.INTERACTION)]
    assert should_initiate_chat(interaction, True, False, 60.0, 2000.0) is True


def test_should_initiate_chat_each_condition() -> None:
    interaction = [_desire(DesireType.INTERACTION)]
    exploration = [_desire(DesireType.EXPLORATION)]
    assert should_initiate_chat(exploration, True, False, 60.0, 2000.0) is False
    assert should_initiate_chat(interaction, False, False, 60.0, 2000.0) is False
    assert should_initiate_chat(interaction, True, True, 60.0, 2000.0) is False
    assert should_initiate_chat(interaction, True, False, 49.0, 2000.0) is False
    assert should_initiate_chat(interaction, True, False, 60.0, 1000.0) is False
