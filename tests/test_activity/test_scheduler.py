from nyx.activity.scheduler import (
    build_schedule,
    desire_to_activity,
    format_time_label,
    rank_desires,
)
from nyx.config import ActivityEnergyDelta
from nyx.enums import ActivityType, DesireType
from nyx.inner_life.emotion import ENERGY_REST_THRESHOLD
from nyx.types import DesireValue, ShortTermDesire


def _desire(id: str, type_: DesireType, created_at: float) -> ShortTermDesire:
    return ShortTermDesire(
        id=id,
        created_at=created_at,
        type=type_,
        strength=0.5,
        description="d",
        goal=None,
    )


def _value(type_: DesireType, weight: float) -> DesireValue:
    return DesireValue(
        type=type_,
        value=0.0,
        expression_weight=weight,
        suppression_threshold=0.5,
        updated_at=0.0,
    )


# ---- desire_to_activity ----

def test_desire_to_activity() -> None:
    assert desire_to_activity(DesireType.EXPLORATION) is ActivityType.READING
    assert desire_to_activity(DesireType.CREATION) is ActivityType.CREATION
    assert desire_to_activity(DesireType.REST) is ActivityType.REST
    assert desire_to_activity(DesireType.INTERACTION) is None


# ---- rank_desires ----

def test_rank_desires() -> None:
    a = _desire("a", DesireType.CREATION, 1.0)
    b = _desire("b", DesireType.EXPLORATION, 2.0)
    c = _desire("c", DesireType.REST, 3.0)
    values = [
        _value(DesireType.CREATION, 0.7),
        _value(DesireType.EXPLORATION, 0.9),
        _value(DesireType.REST, 0.5),
    ]
    assert [d.id for d in rank_desires([a, b, c], values)] == ["b", "a", "c"]


def test_rank_desires_stable_fifo() -> None:
    a = _desire("a", DesireType.EXPLORATION, 2.0)
    b = _desire("b", DesireType.EXPLORATION, 1.0)
    values = [_value(DesireType.EXPLORATION, 0.9)]
    assert [d.id for d in rank_desires([a, b], values)] == ["b", "a"]


def test_rank_desires_missing_value_defaults_zero() -> None:
    a = _desire("a", DesireType.CREATION, 1.0)
    b = _desire("b", DesireType.EXPLORATION, 2.0)
    values = [_value(DesireType.EXPLORATION, 0.9)]
    assert [d.id for d in rank_desires([a, b], values)] == ["b", "a"]


def test_rank_desires_empty() -> None:
    assert rank_desires([], []) == []


# ---- build_schedule ----

def test_build_schedule_empty() -> None:
    assert build_schedule([], 100.0, ActivityEnergyDelta()) == []


def test_build_schedule_enough_energy_preserves_order() -> None:
    desires = [
        _desire("a", DesireType.EXPLORATION, 1.0),
        _desire("b", DesireType.CREATION, 2.0),
        _desire("c", DesireType.REST, 3.0),
    ]
    result = build_schedule(desires, 100.0, ActivityEnergyDelta())
    assert result == [ActivityType.READING, ActivityType.CREATION, ActivityType.REST]


def test_build_schedule_inserts_rest_when_low_energy() -> None:
    desires = [_desire("a", DesireType.EXPLORATION, 1.0)]
    result = build_schedule(desires, 30.0, ActivityEnergyDelta())
    assert result == [ActivityType.REST, ActivityType.READING]


def test_build_schedule_multiple_rest_when_exhausted() -> None:
    desires = [_desire("a", DesireType.EXPLORATION, 1.0)]
    result = build_schedule(desires, 0.0, ActivityEnergyDelta())
    assert result == [ActivityType.REST, ActivityType.REST, ActivityType.READING]


def test_build_schedule_skips_interaction() -> None:
    desires = [
        _desire("a", DesireType.INTERACTION, 1.0),
        _desire("b", DesireType.EXPLORATION, 2.0),
    ]
    result = build_schedule(desires, 100.0, ActivityEnergyDelta())
    assert result == [ActivityType.READING]


def test_build_schedule_rest_nonpositive_no_loop() -> None:
    delta = ActivityEnergyDelta(rest=0)
    desires = [_desire("a", DesireType.EXPLORATION, 1.0)]
    result = build_schedule(desires, 0.0, delta)
    assert result == [ActivityType.READING]


# ---- format_time_label ----

def test_format_time_label() -> None:
    assert format_time_label(0, 60, 9.0) == "09:00"
    assert format_time_label(1, 60, 9.0) == "10:00"
    assert format_time_label(2, 60, 9.5) == "11:30"
    assert format_time_label(0, 30, 0.0) == "00:00"


def test_format_time_label_rounds_float_minutes() -> None:
    # start_hour 浮点乘法落在整数下方一点点，round 而非 int 截断（否则少一分钟）
    assert format_time_label(0, 60, 4.1) == "04:06"
    assert format_time_label(0, 60, 8.2) == "08:12"
    assert format_time_label(0, 60, 16.4) == "16:24"
    assert format_time_label(0, 60, 16.9) == "16:54"


# ---- 常量 ----

def test_rest_energy_threshold() -> None:
    assert 0.0 <= ENERGY_REST_THRESHOLD <= 100.0
