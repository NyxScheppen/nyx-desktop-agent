"""21-reading-impulse 纯函数层单元测试（无 IO 无 LLM）。

验证关键词密度提取、6 驱动「现算」、复合加权、阈值+冷却判定。
冷却判定用显式 `now` 注入，不依赖真实时钟。
"""

from nyx.enums import ReadingBehavior, ReadingDrive
from nyx.reading.impulse import (
    build_drives,
    check_triggers,
    compute_composite,
    extract,
)


def test_extract_rich_paragraph_detects_features() -> None:
    text = "他绝望地哭，问生命的意义是什么？为什么自由如此虚无！她说……"
    features = extract(text)
    assert features.philosophical > 0
    assert features.negative_emo > 0
    assert features.exclamation_ratio > 0
    assert features.character_mention > 0
    assert 0.0 <= features.richness_score <= 1.0


def test_extract_rich_scores_higher_than_flat() -> None:
    rich = extract("他绝望地哭，问生命的意义是什么？为什么自由如此虚无！她说……")
    flat = extract("今天天气不错。")
    assert rich.richness_score > flat.richness_score


def test_build_drives_energy_and_agreeableness() -> None:
    flat = extract("今天天气不错。")  # 无情感/角色内容
    drives = build_drives(
        flat, energy=100.0, agreeableness=10.0,
        exploration_value=0.5, interaction_value=0.3,
    )
    assert drives[ReadingDrive.MOTIVATION] == 1.0
    assert drives[ReadingDrive.EMPATHY_BIAS] == 0.6  # 0.6×(10/10) + 0
    assert drives[ReadingDrive.CURIOSITY] == 0.5
    assert drives[ReadingDrive.BOREDOM] == 0.3


def test_build_drives_emotional_paragraph_raises_empathy() -> None:
    flat = extract("今天天气不错。")
    emotional = extract("他绝望地哭，她悲伤地流泪，孤独地挣扎。")
    flat_bias = build_drives(
        flat, energy=80.0, agreeableness=10.0,
        exploration_value=0.5, interaction_value=0.3,
    )[ReadingDrive.EMPATHY_BIAS]
    emo_bias = build_drives(
        emotional, energy=80.0, agreeableness=10.0,
        exploration_value=0.5, interaction_value=0.3,
    )[ReadingDrive.EMPATHY_BIAS]
    assert emo_bias > flat_bias


def test_compute_composite_weights_spot_check() -> None:
    drives: dict[ReadingDrive, float] = {
        ReadingDrive.MOTIVATION: 0.5,
        ReadingDrive.CURIOSITY: 0.6,
        ReadingDrive.BOREDOM: 0.3,
        ReadingDrive.AESTHETIC_SENSITIVITY: 0.5,
        ReadingDrive.EMPATHY_BIAS: 0.6,
        ReadingDrive.ASSOCIATIVE_DRIVE: 0.4,
    }
    composite = compute_composite(drives)
    # question_knowledge = 0.6×0.5 + 0.4×0.3 + 0.5×0.2 = 0.52
    assert composite[ReadingBehavior.QUESTION_KNOWLEDGE] == 0.52
    # associate = 0.4×0.6 + 0.6×0.2 + 0.6×0.2 = 0.48
    assert composite[ReadingBehavior.ASSOCIATE] == 0.48


def test_check_triggers_above_threshold_fires() -> None:
    composite = {ReadingBehavior.QUESTION_KNOWLEDGE: 0.6}
    assert check_triggers(composite, {}, now=1000.0) == [
        ReadingBehavior.QUESTION_KNOWLEDGE
    ]


def test_check_triggers_within_cooldown_suppressed() -> None:
    composite = {ReadingBehavior.QUESTION_KNOWLEDGE: 0.6}
    cooldowns = {ReadingBehavior.QUESTION_KNOWLEDGE: 1000.0}
    assert check_triggers(composite, cooldowns, now=1000.0) == []


def test_check_triggers_below_threshold_suppressed() -> None:
    composite = {ReadingBehavior.QUESTION_KNOWLEDGE: 0.1}
    assert check_triggers(composite, {}, now=1000.0) == []
