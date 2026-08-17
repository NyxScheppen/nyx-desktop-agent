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
