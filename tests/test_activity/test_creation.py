from nyx.activity.creation import (
    CREATION_STYLES,
    build_creation_context,
    build_creation_system,
    pick_creation_style,
)
from nyx.enums import (
    ActivityStatus,
    ActivityType,
    DesireStatus,
    DesireType,
    EmotionCategory,
    EnergyState,
    MemoryType,
)
from nyx.types import (
    Activity,
    CurrentState,
    Memory,
    Personality,
    ShortTermDesire,
    Values,
)

_PERSONALITY: Personality = {
    "openness": 5.0,
    "conscientiousness": 5.0,
    "extraversion": 5.0,
    "agreeableness": 5.0,
    "neuroticism": 5.0,
}

_VALUES: Values = {
    "attitude_to_human": 5.0,
    "ai_identity_acceptance": 5.0,
    "altruism": 5.0,
    "optimism": 5.0,
}


def _activity() -> Activity:
    return Activity(
        id="a1",
        type=ActivityType.CREATION,
        schedule_block_id="09:00",
        status=ActivityStatus.PENDING,
        progress={
            "goal": {"action": "write", "count": 1, "topic": "骑士团"},
            "desire_id": "d1",
            "correlation_id": "c1",
        },
        started_at=1.0,
    )


def _state() -> CurrentState:
    return CurrentState(
        valence=0.0,
        arousal=0.0,
        emotion=EmotionCategory.NEUTRAL,
        personality=_PERSONALITY,
        values=_VALUES,
        aesthetic={
            "ornate": 7.0,
            "lyrical": 7.0,
            "classical": 6.0,
            "somber": 6.0,
        },
        energy=70.0,
        energy_state=EnergyState.OKAY,
        current_activity=None,
        active_desires=[
            ShortTermDesire(
                id="d1",
                created_at=1.0,
                type=DesireType.CREATION,
                strength=0.8,
                description="写点东西",
                goal=None,
                status=DesireStatus.PENDING,
            )
        ],
    )


def _memory() -> Memory:
    return Memory(
        id="m1",
        created_at=1.0,
        content="成立于 1147 年",
        tag="knowledge",
        summary="骑士团",
        freshness=1.0,
        type=MemoryType.LONG_TERM,
    )


def test_pick_creation_style_uses_known_style_pool() -> None:
    assert pick_creation_style() in CREATION_STYLES


def test_build_creation_context_full() -> None:
    ctx = build_creation_context(
        _activity(),
        "日记体",
        [_memory()],
        {"presence": "online", "window_title": "编辑器", "screen_summary": "写代码"},
    )
    assert "风格：日记体" in ctx
    assert "主题：骑士团" in ctx
    assert "知识库参考" in ctx
    assert "成立于 1147 年" in ctx
    assert "当前屏幕灵感" in ctx


def test_build_creation_context_empty() -> None:
    activity = _activity()
    activity.progress = {}
    ctx = build_creation_context(
        activity, "日记体", [], {"presence": "away", "window_title": ""}
    )
    assert ctx == "风格：日记体"


def test_build_creation_system_injects_state_and_voice() -> None:
    sys = build_creation_system("测试人格", _state())
    assert "测试人格" in sys
    assert "[此刻心境]" in sys
    assert "neutral" in sys
    assert "精力：70/100" in sys
    assert "写点东西" in sys
    assert "[创作要求]" in sys
    assert "按 JSON 输出" in sys
