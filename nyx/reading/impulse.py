"""阅读冲动引擎纯函数层（reading-system spec）。

关键词表 / 权重 / 阈值照搬 S06（feature_extractor / composite_engine）。
5 驱动「现算」、复合加权、阈值+冷却判定；四类提问在 V1 共享冷却并互斥
选择。全部同步纯函数，无 IO 无 LLM。

密度校准：S06 的原始密度 ~0.01 量级，直接进 richness 加权后阈值 0.5 永不可达
（latent bug）。V1 对原始密度先 `_saturate` 线性饱和到 [0,1]（`_DENSITY_CAP` 为
「明显存在」密度阈值，0.02 使典型富段落饱和到 ~0.5-1.0），`uniqueness` 本已 0-1
不再饱和。
"""

from __future__ import annotations

from dataclasses import dataclass

from nyx.enums import ReadingBehavior, ReadingDrive
from nyx.policy import policy

# ---- 关键词集（照搬 S06 feature_extractor.py，逐字复制）----

NEGATIVE_WORDS = policy("reading", "impulse.NEGATIVE_WORDS", set[str])

POSITIVE_WORDS = policy("reading", "impulse.POSITIVE_WORDS", set[str])

PHILOSOPHICAL_WORDS = policy("reading", "impulse.PHILOSOPHICAL_WORDS", set[str])

SENSORY_WORDS = policy("reading", "impulse.SENSORY_WORDS", set[str])

CHARACTER_MARKERS = policy("reading", "impulse.CHARACTER_MARKERS", set[str])

# ---- 密度校准 / mutter 闸门（模块常量，见阅读系统 spec「关键决策」）----

_DENSITY_CAP = policy(
    "reading", "impulse._DENSITY_CAP", float
)  # 「明显存在」密度阈值，饱和到 ~0.5
MUTTER_RICHNESS_THRESHOLD = policy(
    "reading", "impulse.MUTTER_RICHNESS_THRESHOLD", float
)  # richness_score 越过即碎碎念
MUTTER_COOLDOWN_SEC = policy(
    "reading", "impulse.MUTTER_COOLDOWN_SEC", int
)  # mutter 冷却（替代 S06 的 S12 should_mutter）

# ---- 复合权重 / 阈值 / 冷却（照搬 S06 composite_engine.py，5 行为无 mutter）----

QUESTION_BEHAVIORS = policy(
    "reading", "impulse.QUESTION_BEHAVIORS", tuple[ReadingBehavior, ...]
)
QUESTION_COOLDOWN_SEC = policy("reading", "impulse.QUESTION_COOLDOWN_SEC", int)

DEFAULT_COMPOSITE_WEIGHTS: dict[ReadingBehavior, dict[ReadingDrive, float]] = policy(
    "reading",
    "impulse.DEFAULT_COMPOSITE_WEIGHTS",
    dict[ReadingBehavior, dict[ReadingDrive, float]],
)

DEFAULT_THRESHOLDS: dict[ReadingBehavior, float] = policy(
    "reading", "impulse.DEFAULT_THRESHOLDS", dict[ReadingBehavior, float]
)

DEFAULT_COOLDOWNS_SEC: dict[ReadingBehavior, int] = policy(
    "reading", "impulse.DEFAULT_COOLDOWNS_SEC", dict[ReadingBehavior, int]
)


@dataclass
class ParagraphFeatures:
    """段落特征向量（照搬 S06，砍掉 char_count/sentence_count 等 4 个未消费字段）。"""

    exclamation_ratio: float     # 感叹号密度（！/! / 字符数）
    quote_ratio: float           # 引号/对话标记密度（"/「 / 字符数）
    dash_ratio: float            # 破折号/省略号密度（——/…/... / 字符数）
    negative_emo: float          # 负面情绪关键词密度
    positive_emo: float          # 正面情绪关键词密度
    philosophical: float         # 哲学关键词密度
    sensory: float               # 感官关键词密度
    character_mention: float     # 角色/人称密度
    uniqueness: float            # 字频倒数均值（0-1，出现 1 次的 CJK 字占比）
    richness_score: float        # 0-1 综合「丰富度」加权和


def _count_keywords(text: str, keywords: set[str]) -> int:
    return sum(text.count(w) for w in keywords)


def _estimate_uniqueness(text: str) -> float:
    """生僻字（出现 1 次）占 CJK 字符种类的比例（照搬 S06）。"""
    if not text:
        return 0.0
    counts: dict[str, int] = {}
    for ch in text:
        if (
            policy("reading", "impulse.copy.estimate_uniqueness_text", str)
            <= ch
            <= policy("reading", "impulse.copy.estimate_uniqueness_text_2", str)
        ):
            counts[ch] = counts.get(ch, 0) + 1
    if not counts:
        return 0.0
    unique_chars = sum(1 for v in counts.values() if v == 1)
    return min(unique_chars / len(counts), 1.0)


def _saturate(x: float, cap: float) -> float:
    """线性饱和到 [0,1]：密度 x 相对 cap 的占比，超 cap 封顶 1.0。"""
    return min(x / cap, 1.0)


def extract(text: str) -> ParagraphFeatures:
    """从段落文本提取特征向量（同步纯函数）。"""
    denom = max(len(text), 1)

    exclamation = (text.count("！") + text.count("!")) / denom
    # S06 对 `"` 双计是 bug，V1 单计 + 日式引号
    quote = (text.count('"') + text.count("「")) / denom
    dash = (text.count("——") + text.count("…") + text.count("...")) / denom

    negative = _count_keywords(text, NEGATIVE_WORDS) / denom
    positive = _count_keywords(text, POSITIVE_WORDS) / denom
    philosophical = _count_keywords(text, PHILOSOPHICAL_WORDS) / denom
    sensory = _count_keywords(text, SENSORY_WORDS) / denom
    character = _count_keywords(text, CHARACTER_MARKERS) / denom

    uniqueness = _estimate_uniqueness(text)

    # richness 校准：输入特征先饱和再按 S06 同款权重加权，clamp [0,1]。
    richness = (
        policy("reading", "impulse.parameters.richness_emotion_weight", float)
        * _saturate(philosophical, _DENSITY_CAP)
        + policy("reading", "impulse.parameters.richness_dash_weight", float)
        * _saturate(negative + positive, _DENSITY_CAP)
        + policy("reading", "impulse.parameters.richness_philosophical_weight", float)
        * _saturate(dash, _DENSITY_CAP)
        + policy("reading", "impulse.parameters.richness_exclamation_weight", float)
        * _saturate(exclamation, _DENSITY_CAP)
        + policy("reading", "impulse.parameters.richness_uniqueness_weight", float)
        * uniqueness
        + policy("reading", "impulse.parameters.richness_quote_weight", float)
        * _saturate(quote, _DENSITY_CAP)
    )

    return ParagraphFeatures(
        exclamation_ratio=exclamation,
        quote_ratio=quote,
        dash_ratio=dash,
        negative_emo=negative,
        positive_emo=positive,
        philosophical=philosophical,
        sensory=sensory,
        character_mention=character,
        uniqueness=uniqueness,
        richness_score=min(richness, 1.0),
    )


def associative_density(features: ParagraphFeatures) -> float:
    """记忆联想密度：哲学/感官/情感三源的加权和，饱和到 [0,1]。"""
    raw = (
        policy("reading", "impulse.parameters.association_philosophical_weight", float)
        * features.philosophical
        + policy("reading", "impulse.parameters.association_sensory_weight", float)
        * features.sensory
        + policy("reading", "impulse.parameters.association_emotion_weight", float)
        * (features.negative_emo + features.positive_emo)
    )
    return _saturate(raw, _DENSITY_CAP)


def empathy_density(features: ParagraphFeatures) -> float:
    """共鸣密度：情感密度 + 角色提及密度，饱和到 [0,1]。"""
    raw = (
        policy("reading", "impulse.parameters.empathy_emotion_weight", float)
        * (features.negative_emo + features.positive_emo)
        + policy("reading", "impulse.parameters.empathy_character_weight", float)
        * features.character_mention
    )
    return _saturate(raw, _DENSITY_CAP)


def build_drives(
    features: ParagraphFeatures,
    *,
    energy: float,
    exploration_value: float,
    interaction_value: float,
) -> dict[ReadingDrive, float]:
    """5 驱动「现算」（全部归一到 [0,1]，无累积无衰减）。"""
    return {
        ReadingDrive.MOTIVATION: energy / policy("inner_life", "energy.maximum", float),
        ReadingDrive.CURIOSITY: exploration_value,
        ReadingDrive.BOREDOM: interaction_value,
        ReadingDrive.ASSOCIATIVE_DRIVE: policy(
            "reading", "impulse.parameters.association_baseline", float
        )
        + policy("reading", "impulse.parameters.association_density_weight", float)
        * associative_density(features),
        ReadingDrive.EMPATHY_BIAS: empathy_density(features),
    }


def compute_composite(
    drives: dict[ReadingDrive, float],
) -> dict[ReadingBehavior, float]:
    """每行为复合值 = Σ(驱动值 × 权重)。"""
    return {
        behavior: sum(
            drives.get(drive, 0.0) * weight
            for drive, weight in behavior_weights.items()
        )
        for behavior, behavior_weights in DEFAULT_COMPOSITE_WEIGHTS.items()
    }


def check_triggers(
    composite: dict[ReadingBehavior, float],
    cooldowns: dict[ReadingBehavior, float],
    now: float,
    *,
    question_cooldown_at: float = 0.0,
    question_pending: bool = False,
) -> list[ReadingBehavior]:
    """返回越过阈值且不在冷却中的行为列表。

    四类提问共享一个冷却窗口，每段只保留分数最高的提问；联想仍独立
    使用自己的冷却。`now` 和冷却时间戳显式注入，便于纯函数测试。
    """
    triggered: list[ReadingBehavior] = []

    question_candidates = [
        behavior
        for behavior in QUESTION_BEHAVIORS
        if composite.get(behavior, 0.0) >= DEFAULT_THRESHOLDS[behavior]
    ]
    question_in_cooldown = question_pending or (
        question_cooldown_at > 0
        and now < question_cooldown_at + QUESTION_COOLDOWN_SEC
    )
    if question_candidates and not question_in_cooldown:
        triggered.append(
            max(question_candidates, key=lambda behavior: composite[behavior])
        )

    associate = ReadingBehavior.ASSOCIATE
    associate_last_at = cooldowns.get(associate, 0.0)
    associate_in_cooldown = (
        associate_last_at > 0
        and now < associate_last_at + DEFAULT_COOLDOWNS_SEC[associate]
    )
    if (
        composite.get(associate, 0.0) >= DEFAULT_THRESHOLDS[associate]
        and not associate_in_cooldown
    ):
        triggered.append(associate)
    return triggered
