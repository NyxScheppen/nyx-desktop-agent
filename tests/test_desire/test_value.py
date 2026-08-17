"""欲望值机制纯函数测试（无 DB、无 mock）。"""
import pytest

from nyx.desire.value import (
    REFUND_DELTA,
    SUPPRESSION_RAISE_DELTA,
    WEIGHT_REINFORCE_DELTA,
    apply_pressure,
    at_peak,
    decay_value,
    default_value,
    is_expressible,
    raise_suppression,
    reinforce_weight,
)
from nyx.enums import DesireType


def test_decay_value() -> None:
    assert decay_value(1.0, 0.0, 0.02) == 1.0          # 不衰减
    assert decay_value(1.0, 1.0, 0.02) == pytest.approx(0.98)
    assert decay_value(0.01, 1.0, 0.5) == 0.0          # 夹到 0
    assert decay_value(0.5, 10.0, 0.0) == 0.5          # rate=0 不变


def test_apply_pressure() -> None:
    assert apply_pressure(0.3, 0.2) == pytest.approx(0.5)
    assert apply_pressure(0.9, 0.5) == 1.0             # 夹到上限
    assert apply_pressure(0.1, -0.5) == 0.0            # 负 delta 夹到下限


def test_reinforce_weight() -> None:
    assert reinforce_weight(0.7) == pytest.approx(0.7 + WEIGHT_REINFORCE_DELTA)
    assert reinforce_weight(0.7, 0.1) == pytest.approx(0.8)   # 显式覆盖
    assert reinforce_weight(0.99) == 1.0                     # 夹到上限


def test_raise_suppression() -> None:
    assert raise_suppression(0.5) == pytest.approx(0.5 + SUPPRESSION_RAISE_DELTA)
    assert raise_suppression(0.5, 0.2) == pytest.approx(0.7)   # 显式覆盖
    assert raise_suppression(0.95) == 1.0                     # 夹到上限


def test_at_peak() -> None:
    assert at_peak(0.9, 0.8) is True
    assert at_peak(0.7, 0.8) is False
    assert at_peak(0.8, 0.8) is True     # 含等号


def test_is_expressible() -> None:
    assert is_expressible(0.9, 0.5) is True
    assert is_expressible(0.4, 0.5) is False
    assert is_expressible(0.5, 0.5) is True   # 含等号


def test_gating_suppression() -> None:
    peak = 0.8
    # 初始：suppression=0.5，达峰即表达
    assert at_peak(0.8, peak) is True
    assert is_expressible(0.8, 0.5) is True
    # 失败 4 次：suppression 0.5 → 0.9，达峰也不再表达（越挫越压抑）
    suppression = 0.5
    for _ in range(4):
        suppression = raise_suppression(suppression)
    assert suppression == pytest.approx(0.9)
    assert at_peak(0.8, peak) is True
    assert is_expressible(0.8, suppression) is False


def test_default_value() -> None:
    for t in DesireType:
        dv = default_value(t)
        assert dv.type is t
        assert dv.value == 0.0
        assert dv.expression_weight == 0.7
        assert dv.suppression_threshold == 0.5
        assert dv.updated_at == 0.0


def test_step_constants() -> None:
    assert 0.0 <= WEIGHT_REINFORCE_DELTA <= SUPPRESSION_RAISE_DELTA
    assert REFUND_DELTA > 0
