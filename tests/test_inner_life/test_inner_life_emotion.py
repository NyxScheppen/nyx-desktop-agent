from nyx.enums import ActivityType, EmotionCategory, EnergyState, EventType
from nyx.inner_life.emotion import (
    apply_offset,
    clamp_arousal,
    clamp_valence,
    decay_emotion,
    event_offset,
    resolve_emotion,
    vad_to_category,
)
from nyx.inner_life.facade import energy_to_state


def test_clamp() -> None:
    assert clamp_valence(1.5) == 1.0
    assert clamp_valence(-1.5) == -1.0
    assert clamp_valence(0.3) == 0.3
    assert clamp_arousal(1.5) == 1.0
    assert clamp_arousal(-0.5) == 0.0
    assert clamp_arousal(0.4) == 0.4


def test_decay_emotion() -> None:
    assert decay_emotion(0.8, 0.6, 0.0, 0.5) == (0.8, 0.6)
    assert decay_emotion(0.8, 0.6, 1.0, 0.0) == (0.8, 0.6)
    assert decay_emotion(0.8, 0.6, 2.0, 0.5) == (0.0, 0.0)
    v, _ = decay_emotion(-0.8, 0.0, 1.0, 0.5)
    assert v == -0.4


def test_apply_offset() -> None:
    v, a = apply_offset(0.5, 0.5, 0.6, 0.1)
    assert v == 1.0 and a == 0.6
    v, a = apply_offset(0.5, 0.5, -2.0, -2.0)
    assert v == -1.0 and a == 0.0


def test_event_offset() -> None:
    assert event_offset(EventType.DESIRE_SATISFIED) == (0.2, 0.1)
    assert event_offset(EventType.USER_MESSAGE) == (0.0, 0.0)


def test_vad_to_category() -> None:
    assert vad_to_category(0.9, 0.8) is EmotionCategory.HAPPY
    assert vad_to_category(0.9, 0.2) is EmotionCategory.SHY
    assert vad_to_category(-0.9, 0.8) is EmotionCategory.ANGRY
    assert vad_to_category(-0.9, 0.4) is EmotionCategory.WORRIED
    assert vad_to_category(-0.9, 0.2) is EmotionCategory.SAD
    assert vad_to_category(0.0, 0.2) is EmotionCategory.NEUTRAL


def test_vad_boundary() -> None:
    assert vad_to_category(0.2, 0.5) is EmotionCategory.NEUTRAL
    assert vad_to_category(-0.2, 0.5) is EmotionCategory.NEUTRAL


def test_resolve_emotion() -> None:
    assert (
        resolve_emotion(EmotionCategory.HAPPY, EnergyState.DRAINED, None)
        is EmotionCategory.SLEEPY
    )
    assert (
        resolve_emotion(
            EmotionCategory.NEUTRAL,
            EnergyState.ENERGETIC,
            ActivityType.IDLE_REFLECTION,
        )
        is EmotionCategory.THINKING
    )
    assert (
        resolve_emotion(
            EmotionCategory.HAPPY, EnergyState.OKAY, ActivityType.READING
        )
        is EmotionCategory.HAPPY
    )
    assert (
        resolve_emotion(EmotionCategory.HAPPY, EnergyState.OKAY, None)
        is EmotionCategory.HAPPY
    )


def test_energy_to_state() -> None:
    assert energy_to_state(100.0) is EnergyState.ENERGETIC
    assert energy_to_state(79.0) is EnergyState.OKAY
    assert energy_to_state(59.0) is EnergyState.TIRED
    assert energy_to_state(39.0) is EnergyState.EXHAUSTED
    assert energy_to_state(19.0) is EnergyState.DRAINED


def test_energy_to_state_boundary() -> None:
    assert energy_to_state(80.0) is EnergyState.ENERGETIC
    assert energy_to_state(60.0) is EnergyState.OKAY
    assert energy_to_state(40.0) is EnergyState.TIRED
    assert energy_to_state(20.0) is EnergyState.EXHAUSTED
