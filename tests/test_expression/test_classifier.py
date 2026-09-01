from nyx.enums import ContextMode, EmotionCategory, EnergyState
from nyx.expression.classifier import classify_channel, slow_score
from nyx.types import CurrentState, Personality, Values

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


def _state(*, energy: float, arousal: float) -> CurrentState:
    return CurrentState(
        valence=0.5,
        arousal=arousal,
        emotion=EmotionCategory.NEUTRAL,
        personality=_PERSONALITY,
        values=_VALUES,
        aesthetic={
            "ornate": 7.0, "lyrical": 7.0, "classical": 6.0, "somber": 6.0,
        },
        energy=energy,
        energy_state=EnergyState.OKAY,
        current_activity=None,
        active_desires=[],
    )


def test_slow_score_in_range() -> None:
    low = slow_score("", _state(energy=0.0, arousal=1.0), now=100.0, last_slow_at=100.0)
    high = slow_score(
        "你能帮我解释一下为什么吗？我今天有点难过",
        _state(energy=100.0, arousal=0.0),
        now=7300.0,
        last_slow_at=100.0,
    )
    clock_back = slow_score(
        "", _state(energy=0.0, arousal=1.0), now=100.0, last_slow_at=200.0
    )
    assert 0.0 <= low < 0.5
    assert 0.5 <= high <= 1.0
    assert clock_back >= 0.0


def test_slow_score_factors() -> None:
    mid = _state(energy=50.0, arousal=0.5)
    calm = _state(energy=100.0, arousal=0.0)
    tense = _state(energy=0.0, arousal=1.0)
    assert slow_score("x", mid, 0.0, 0.0) < slow_score("x" * 60, mid, 0.0, 0.0)
    assert slow_score("x", mid, 0.0, 0.0) < slow_score("吗", mid, 0.0, 0.0)
    assert slow_score("xx", mid, 0.0, 0.0) < slow_score("难过", mid, 0.0, 0.0)
    assert slow_score("x", calm, 0.0, 0.0) > slow_score("x", tense, 0.0, 0.0)
    assert slow_score("x", mid, 7200.0, 0.0) > slow_score("x", mid, 0.0, 0.0)


def test_classify_channel() -> None:
    slow_state = _state(energy=100.0, arousal=0.0)
    fast_state = _state(energy=20.0, arousal=0.9)
    assert classify_channel("在吗", slow_state, 7200.0, 0.0, 0.5) == ContextMode.SLOW
    assert classify_channel("哦", fast_state, 60.0, 0.0, 0.5) == ContextMode.FAST


def test_emotion_words_no_single_char_false_positive() -> None:
    # 单字「累」「烦」曾是子串误判源：同长度中性词对比，「积累」「麻烦」不触发情感
    mid = _state(energy=50.0, arousal=0.5)
    neutral = slow_score("项目", mid, 0.0, 0.0)
    assert slow_score("积累", mid, 0.0, 0.0) == neutral   # 含「累」但不该命中
    assert slow_score("麻烦", mid, 0.0, 0.0) == neutral   # 含「烦」但不该命中
    # 双字情感词正常命中
    assert slow_score("烦躁", mid, 0.0, 0.0) > neutral
    assert slow_score("疲惫", mid, 0.0, 0.0) > neutral
