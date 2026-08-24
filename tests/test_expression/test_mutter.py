# pyright: reportPrivateUsage=false
from nyx.enums import DesireType
from nyx.expression.mutter import (
    _MUTTER_TEMPLATES,
    MutterCategory,
    pick_mutter_category,
    pick_mutter_template,
    should_initiate_chat,
)
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


def test_templates_four_categories_nonempty_and_unique() -> None:
    assert set(_MUTTER_TEMPLATES) == set(MutterCategory)
    for pool in _MUTTER_TEMPLATES.values():
        assert len(pool) == 10
        assert len(set(pool)) == 10


def test_pick_mutter_category_out_of_range() -> None:
    assert pick_mutter_category(-0.1) is None
    assert pick_mutter_category(1.0) is None


def test_pick_mutter_category_maps_to_four() -> None:
    assert pick_mutter_category(0.0) is MutterCategory.ACTIVITY
    assert pick_mutter_category(0.25) is MutterCategory.MEMORY
    assert pick_mutter_category(0.5) is MutterCategory.DESIRE
    assert pick_mutter_category(0.75) is MutterCategory.USER


def test_pick_mutter_template_out_of_range() -> None:
    assert pick_mutter_template(MutterCategory.ACTIVITY, -0.1) is None
    assert pick_mutter_template(MutterCategory.ACTIVITY, 1.0) is None


def test_pick_mutter_template_bounds_and_membership() -> None:
    assert (
        pick_mutter_template(MutterCategory.MEMORY, 0.0)
        == _MUTTER_TEMPLATES[MutterCategory.MEMORY][0]
    )
    assert (
        pick_mutter_template(MutterCategory.MEMORY, 0.999)
        == _MUTTER_TEMPLATES[MutterCategory.MEMORY][-1]
    )
    assert (
        pick_mutter_template(MutterCategory.USER, 0.37)
        in _MUTTER_TEMPLATES[MutterCategory.USER]
    )


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
