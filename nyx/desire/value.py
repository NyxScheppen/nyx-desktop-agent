from nyx.enums import DesireType
from nyx.types import DesireValue

# —— 压力值 value 范围 ——
_VALUE_MIN = 0.0
_VALUE_MAX = 1.0

# —— 表达权重 expression_weight ——
_WEIGHT_MIN = 0.0
_WEIGHT_MAX = 1.0
_WEIGHT_INIT = 0.7               # 初始表达权重（四类型统一，中性偏高）
WEIGHT_REINFORCE_DELTA = 0.05    # 满足一次 +0.05（正强化）

# —— 抑制阈值 suppression_threshold ——
_SUPPRESSION_MIN = 0.0
_SUPPRESSION_MAX = 1.0
_SUPPRESSION_INIT = 0.5          # 初始抑制阈值（< peak=0.9，初始不压抑）
SUPPRESSION_RAISE_DELTA = 0.1    # 失败/抑制一次 +0.1（习得性抑制）

# —— 回增（放弃/淘汰压力回灌）——
REFUND_DELTA = 0.3


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def decay_value(value: float, elapsed_days: float, rate: float) -> float:
    """压力值线性衰减（rate/天），下限 0。纯函数。"""
    return max(0.0, value - rate * elapsed_days)


def apply_pressure(value: float, delta: float) -> float:
    """加压/回增：value + delta，夹到 [0, 1]。纯函数。"""
    return _clamp(value + delta, _VALUE_MIN, _VALUE_MAX)


def reinforce_weight(weight: float, delta: float = WEIGHT_REINFORCE_DELTA) -> float:
    """满足后表达权重正强化：weight + delta，夹到 [0, 1]。纯函数。"""
    return _clamp(weight + delta, _WEIGHT_MIN, _WEIGHT_MAX)


def raise_suppression(
    threshold: float, delta: float = SUPPRESSION_RAISE_DELTA
) -> float:
    """失败/抑制后抑制阈值上浮：threshold + delta，夹到 [0, 1]。纯函数。"""
    return _clamp(threshold + delta, _SUPPRESSION_MIN, _SUPPRESSION_MAX)


def at_peak(value: float, peak_threshold: float) -> bool:
    """压力是否达峰：value >= peak_threshold。纯函数。"""
    return value >= peak_threshold


def is_expressible(value: float, suppression_threshold: float) -> bool:
    """达峰后是否可表达：value 越过抑制阈值（未被习得性抑制压住）。纯函数。"""
    return value >= suppression_threshold


def default_value(type_: DesireType) -> DesireValue:
    """某类型的初始 DesireValue：value=0、表达权重/抑制阈值用默认基线、
    updated_at 哨兵 0.0。纯函数。"""
    return DesireValue(
        type=type_,
        value=0.0,
        expression_weight=_WEIGHT_INIT,
        suppression_threshold=_SUPPRESSION_INIT,
        updated_at=0.0,     # 哨兵：由 11 初始化时覆盖为 now
    )
