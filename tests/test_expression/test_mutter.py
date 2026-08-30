# pyright: reportPrivateUsage=false
from nyx.enums import ActivityType, DesireType
from nyx.expression.mutter import (
    _MUTTER_SKELETONS,
    MutterCategory,
    activity_subject,
    clean_fragment,
    naturalize_presence,
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


def test_skeletons_four_categories_nonempty() -> None:
    assert set(_MUTTER_SKELETONS) == set(MutterCategory)
    for pool in _MUTTER_SKELETONS.values():
        assert len(pool) == 10
        assert len(set(pool)) == 10
        assert all("{subject}" in s for s in pool)


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
        == _MUTTER_SKELETONS[MutterCategory.MEMORY][0]
    )
    assert (
        pick_mutter_template(MutterCategory.MEMORY, 0.999)
        == _MUTTER_SKELETONS[MutterCategory.MEMORY][-1]
    )
    assert (
        pick_mutter_template(MutterCategory.USER, 0.37)
        in _MUTTER_SKELETONS[MutterCategory.USER]
    )


def test_naturalize_presence_maps_and_never_leaks_raw() -> None:
    assert naturalize_presence("away") == "你走开了"
    assert naturalize_presence("online") == "你在电脑前"
    assert naturalize_presence("busy") == "你在忙"
    assert "away" not in naturalize_presence("away")
    assert naturalize_presence("unknown") == "unknown"


def test_clean_fragment_strips_observation_presence() -> None:
    assert clean_fragment("用户（away）") == "你走开了"
    assert "away" not in clean_fragment("用户（away）")
    assert "away" not in clean_fragment("用户（away）正在浏览 Chrome")


def test_clean_fragment_collapses_and_truncates() -> None:
    assert clean_fragment("  你 喜欢 \n安静  ") == "你 喜欢 安静"
    long = clean_fragment("你是一个非常非常非常非常喜欢安静的人")
    assert long.endswith("…")
    assert len(long) <= 17


def test_activity_subject_specific_referents() -> None:
    assert (
        activity_subject(ActivityType.READING, {"book": "挪威的森林"})
        == "读了《挪威的森林》"
    )
    assert activity_subject(ActivityType.CREATION, {"title": "日记"}) == "写了《日记》"
    assert (
        activity_subject(
            ActivityType.FREE_EXPLORATION, {"core_discovery": "深海鱼会发光"}
        )
        == "发现「深海鱼会发光」"
    )


def test_activity_subject_missing_data_returns_none() -> None:
    assert activity_subject(ActivityType.READING, {}) is None
    assert activity_subject(ActivityType.CREATION, {}) is None
    assert activity_subject(ActivityType.REST, {}) is None


def test_activity_subject_exploration_falls_back_to_summary() -> None:
    assert (
        activity_subject(ActivityType.FREE_EXPLORATION, {"summary": "查到了一些资料"})
        == "查到了一些资料"
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
