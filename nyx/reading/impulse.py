"""阅读冲动引擎纯函数层（21-reading-impulse）。

关键词表 / 权重 / 阈值 / 冷却照搬 S06（feature_extractor / composite_engine）。
6 驱动「现算」、复合加权、阈值+冷却判定。全部同步纯函数，无 IO 无 LLM。

密度校准：S06 的原始密度 ~0.01 量级，直接进 richness 加权后阈值 0.5 永不可达
（latent bug）。V1 对原始密度先 `_saturate` 线性饱和到 [0,1]（`_DENSITY_CAP` 为
「明显存在」密度阈值，0.02 使典型富段落饱和到 ~0.5-1.0），`uniqueness` 本已 0-1
不再饱和。
"""

from __future__ import annotations

from dataclasses import dataclass

from nyx.enums import ReadingBehavior, ReadingDrive

# ---- 关键词集（照搬 S06 feature_extractor.py，逐字复制）----

NEGATIVE_WORDS = {
    "死", "痛", "哭", "泪", "血", "恨", "绝望", "恐惧", "悲伤", "孤独",
    "愤怒", "挣扎", "崩溃", "无助", "黑暗", "死亡",
}

POSITIVE_WORDS = {
    "笑", "美", "暖", "希望", "幸福", "快乐", "温柔", "阳光", "善良",
    "拥抱", "勇气", "平静", "美好", "光芒", "安心",
}

PHILOSOPHICAL_WORDS = {
    "意义", "存在", "生命", "命运", "真理", "原则", "信仰", "自由", "选择",
    "时间", "永恒", "虚无", "本质", "灵魂", "道德", "价值", "为什么", "何为",
}

SENSORY_WORDS = {
    "颜色", "声音", "气味", "温度", "光", "风", "雨", "冷", "热", "湿",
    "干", "软", "硬", "响", "静", "暗", "亮",
}

CHARACTER_MARKERS = {"他", "她", "它", "他们", "她们", "说道", "说", "问", "答道"}

# ---- 密度校准 / mutter 闸门（模块常量，见 spec 21「关键决策」）----

_DENSITY_CAP = 0.02                  # 「明显存在」密度阈值，饱和到 ~0.5
MUTTER_RICHNESS_THRESHOLD = 0.5      # richness_score 越过即碎碎念
MUTTER_COOLDOWN_SEC = 30             # mutter 冷却（替代 S06 的 S12 should_mutter）

# ---- 复合权重 / 阈值 / 冷却（照搬 S06 composite_engine.py，5 行为无 mutter）----

DEFAULT_COMPOSITE_WEIGHTS: dict[ReadingBehavior, dict[ReadingDrive, float]] = {
    ReadingBehavior.QUESTION_KNOWLEDGE: {
        ReadingDrive.CURIOSITY: 0.50,
        ReadingDrive.ASSOCIATIVE_DRIVE: 0.30,
        ReadingDrive.MOTIVATION: 0.20,
    },
    ReadingBehavior.QUESTION_PERSONAL: {
        ReadingDrive.EMPATHY_BIAS: 0.50,
        ReadingDrive.MOTIVATION: 0.30,
        ReadingDrive.CURIOSITY: 0.20,
    },
    ReadingBehavior.QUESTION_REFLECTIVE: {
        ReadingDrive.EMPATHY_BIAS: 0.40,
        ReadingDrive.AESTHETIC_SENSITIVITY: 0.40,
        ReadingDrive.CURIOSITY: 0.20,
    },
    ReadingBehavior.QUOTE_QUESTION: {
        ReadingDrive.CURIOSITY: 0.40,
        ReadingDrive.EMPATHY_BIAS: 0.40,
        ReadingDrive.ASSOCIATIVE_DRIVE: 0.20,
    },
    ReadingBehavior.ASSOCIATE: {
        ReadingDrive.ASSOCIATIVE_DRIVE: 0.60,
        ReadingDrive.CURIOSITY: 0.20,
        ReadingDrive.EMPATHY_BIAS: 0.20,
    },
}

DEFAULT_THRESHOLDS: dict[ReadingBehavior, float] = {
    ReadingBehavior.QUESTION_KNOWLEDGE: 0.55,
    ReadingBehavior.QUESTION_PERSONAL: 0.60,
    ReadingBehavior.QUESTION_REFLECTIVE: 0.55,
    ReadingBehavior.QUOTE_QUESTION: 0.65,
    ReadingBehavior.ASSOCIATE: 0.50,
}

DEFAULT_COOLDOWNS_SEC: dict[ReadingBehavior, int] = {
    ReadingBehavior.QUESTION_KNOWLEDGE: 120,
    ReadingBehavior.QUESTION_PERSONAL: 180,
    ReadingBehavior.QUESTION_REFLECTIVE: 150,
    ReadingBehavior.QUOTE_QUESTION: 180,
    ReadingBehavior.ASSOCIATE: 60,
}


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
        if "一" <= ch <= "鿿":
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
        0.20 * _saturate(philosophical, _DENSITY_CAP)
        + 0.20 * _saturate(negative + positive, _DENSITY_CAP)
        + 0.20 * _saturate(dash, _DENSITY_CAP)
        + 0.15 * _saturate(exclamation, _DENSITY_CAP)
        + 0.15 * uniqueness
        + 0.10 * _saturate(quote, _DENSITY_CAP)
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
        0.5 * features.philosophical
        + 0.33 * features.sensory
        + 0.17 * (features.negative_emo + features.positive_emo)
    )
    return _saturate(raw, _DENSITY_CAP)


def empathy_density(features: ParagraphFeatures) -> float:
    """共鸣密度：情感密度 + 角色提及密度，饱和到 [0,1]。"""
    raw = (
        0.6 * (features.negative_emo + features.positive_emo)
        + 0.4 * features.character_mention
    )
    return _saturate(raw, _DENSITY_CAP)


def build_drives(
    features: ParagraphFeatures,
    *,
    energy: float,
    agreeableness: float,
    exploration_value: float,
    interaction_value: float,
) -> dict[ReadingDrive, float]:
    """6 驱动「现算」（全部归一到 [0,1]，无累积无衰减）。"""
    return {
        ReadingDrive.MOTIVATION: energy / 100.0,
        ReadingDrive.CURIOSITY: exploration_value,
        ReadingDrive.BOREDOM: interaction_value,
        ReadingDrive.ASSOCIATIVE_DRIVE: 0.4 + 0.6 * associative_density(features),
        ReadingDrive.AESTHETIC_SENSITIVITY: features.richness_score,
        ReadingDrive.EMPATHY_BIAS: (
            0.6 * (agreeableness / 10.0) + 0.4 * empathy_density(features)
        ),
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
) -> list[ReadingBehavior]:
    """返回越过阈值且不在冷却中的行为列表（`now` 显式注入，不依赖真实时钟）。"""
    triggered: list[ReadingBehavior] = []
    for behavior, value in composite.items():
        last_at = cooldowns.get(behavior, 0.0)
        in_cooldown = (
            last_at > 0 and now < last_at + DEFAULT_COOLDOWNS_SEC[behavior]
        )
        if value >= DEFAULT_THRESHOLDS[behavior] and not in_cooldown:
            triggered.append(behavior)
    return triggered
