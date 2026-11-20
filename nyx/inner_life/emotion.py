from collections.abc import Mapping
from types import MappingProxyType

from nyx.enums import ActivityType, EmotionCategory, EnergyState, EventType
from nyx.policy import policy

# —— 基线（平静）——
BASELINE_VALENCE = policy("inner_life", "emotion.BASELINE_VALENCE", float)
BASELINE_AROUSAL = policy("inner_life", "emotion.BASELINE_AROUSAL", float)

# —— 情感衰减：每天回基线的比例（"随时间衰减回基线"）——
EMOTION_DECAY_RATE = policy("inner_life", "emotion.EMOTION_DECAY_RATE", float)

# —— 事件 → 情感坐标偏移 (Δvalence, Δarousal)，只读查找表（模块级不可变）——
_OFFSETS: Mapping[EventType, tuple[float, float]] = MappingProxyType(
    policy("inner_life", "emotion._OFFSETS", dict[EventType, tuple[float, float]])
)

# —— vad_to_category 阈值 ——
_V_NEAR = policy("inner_life", "emotion._V_NEAR", float)  # |valence| < 0.2 视为"中性带"
_A_LOW = policy("inner_life", "emotion._A_LOW", float)  # arousal < 0.3 视为"低唤醒"
_A_HIGH = policy("inner_life", "emotion._A_HIGH", float)  # arousal >= 0.6 视为"高唤醒"

# —— 精力休息阈值：TIRED 档下界（energy_to_state 分界，单一来源）——
ENERGY_REST_THRESHOLD = policy(
    "inner_life", "emotion.ENERGY_REST_THRESHOLD", float
)  # TIRED 档下界：< 40 落 EXHAUSTED([20,40)) / DRAINED(<20)

# —— 覆盖阈值 ——
_SLEEPY_STATES = policy("inner_life", "emotion._SLEEPY_STATES", tuple[EnergyState, ...])
_THINKING_ACTIVITIES = policy(
    "inner_life", "emotion._THINKING_ACTIVITIES", tuple[ActivityType, ...]
)


def clamp_valence(v: float) -> float:
    """valence 夹到 [-1, 1]。纯函数。"""
    return max(-1.0, min(1.0, v))


def clamp_arousal(a: float) -> float:
    """arousal 夹到 [0, 1]。纯函数。"""
    return max(0.0, min(1.0, a))


def decay_emotion(
    valence: float, arousal: float, elapsed_days: float, rate: float
) -> tuple[float, float]:
    """情感线性衰减回基线 (0,0)：f = max(0, 1 - rate×elapsed)，两轴同乘 f。纯函数。"""
    f = max(0.0, 1.0 - rate * elapsed_days)
    return valence * f, arousal * f


def apply_offset(
    valence: float, arousal: float, d_valence: float, d_arousal: float
) -> tuple[float, float]:
    """施加情感偏移并 clamp。纯函数。"""
    return clamp_valence(valence + d_valence), clamp_arousal(arousal + d_arousal)


def event_offset(event_type: EventType) -> tuple[float, float]:
    """事件类型 → 情感偏移（未知事件 0 偏移）。纯函数。"""
    return _OFFSETS.get(event_type, (0.0, 0.0))


def vad_to_category(valence: float, arousal: float) -> EmotionCategory:
    """valence/arousal → 6 档情绪（不含 sleepy/thinking 覆盖）。纯函数。"""
    if arousal < _A_LOW:                          # 低唤醒
        if valence > _V_NEAR:
            return EmotionCategory.SHY            # valence+ 低唤醒 → 害羞
        if valence < -_V_NEAR:
            return EmotionCategory.SAD            # valence- 低唤醒 → 难过
        return EmotionCategory.NEUTRAL
    if valence > _V_NEAR:                          # 中高唤醒，valence+
        return EmotionCategory.HAPPY
    if valence < -_V_NEAR:                         # 中高唤醒，valence-
        return EmotionCategory.ANGRY if arousal >= _A_HIGH else EmotionCategory.WORRIED
    return EmotionCategory.NEUTRAL                 # 中性 valence，中高唤醒 → 平静


def resolve_emotion(
    base: EmotionCategory,
    energy_state: EnergyState,
    current_activity: ActivityType | None,
) -> EmotionCategory:
    """最终表情（8 档）：优先级 困倦 > 思考 > 情绪。纯函数。"""
    if energy_state in _SLEEPY_STATES:
        return EmotionCategory.SLEEPY
    if current_activity is not None and current_activity in _THINKING_ACTIVITIES:
        return EmotionCategory.THINKING
    return base
