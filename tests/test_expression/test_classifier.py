from nyx.enums import ContextMode, EmotionCategory, EnergyState
from nyx.expression.classifier import classify_channel, slow_score
from nyx.types import CurrentState, InnerDocument, InnerTopic


def _state(*, energy: float, arousal: float) -> CurrentState:
    return CurrentState(
        valence=0.5,
        arousal=arousal,
        emotion=EmotionCategory.NEUTRAL,
        energy=energy,
        energy_state=EnergyState.OKAY,
        current_activity=None,
        active_desires=[],
        inner_document=InnerDocument(
            [InnerTopic("identity", "self-understanding", "自我理解", "我是尼克斯")],
            1,
            1000.0,
        ),
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


def test_explicit_survey_url_uses_tool_channel() -> None:
    state = _state(energy=0.0, arousal=1.0)
    assert classify_channel(
        "去 https://8valuescn.pages.dev/ 做问卷", state, 60.0, 0.0, 0.5
    ) is ContextMode.SLOW


def test_explicit_activity_requests_use_tool_channel() -> None:
    state = _state(energy=0.0, arousal=1.0)
    for message in ("读到第 20 段", "写一篇关于雨的随笔", "打开网页看看这篇文章"):
        assert classify_channel(message, state, 60.0, 0.0, 0.5) is ContextMode.SLOW


def test_emotion_words_no_single_char_false_positive() -> None:
    # 单字「累」「烦」曾是子串误判源：同长度中性词对比，「积累」「麻烦」不触发情感
    mid = _state(energy=50.0, arousal=0.5)
    neutral = slow_score("项目", mid, 0.0, 0.0)
    assert slow_score("积累", mid, 0.0, 0.0) == neutral   # 含「累」但不该命中
    assert slow_score("麻烦", mid, 0.0, 0.0) == neutral   # 含「烦」但不该命中
    # 双字情感词正常命中
    assert slow_score("烦躁", mid, 0.0, 0.0) > neutral
    assert slow_score("疲惫", mid, 0.0, 0.0) > neutral
