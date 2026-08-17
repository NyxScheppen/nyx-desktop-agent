# 欲望值机制（desire-value）

> 范围：`desire/value.py`（值机制的纯函数 + 范围/初始/步长常量 + `default_value()`）。
> 纯基础设施 spec：只做「压力值 + 表达权重 + 抑制阈值」的数学语义，不含 store（desire_value 三表的 CRUD 归 11）、不含 Facade、不含 LLM 生成、不含生命周期编排（11-desire）。
> **本文件自包含**：`desire/value.py` 完整代码内联在下文。

## 元信息

- **前置依赖**：01-types（`DesireType` / `DesireValue`）、02-config（`DesireConfig.peak_threshold` / `DesireConfig.value_decay`——作为纯函数参数来源，本 spec 不 import config，只定义纯函数）

## 用户故事

> 作为 Nyx 系统的开发者，我想要欲望值机制的纯函数层——压力值衰减/加压/达峰判定/表达门控/正强化/习得性抑制，以便 11-desire 的 `DesireFacade` 编排时调这些纯函数、前端/评审能直接读懂「尼克斯为什么越挫越压抑」。

## 验收标准

- [ ] `value.py` 含 7 个纯函数（`decay_value` / `apply_pressure` / `reinforce_weight` / `raise_suppression` / `at_peak` / `is_expressible` / `default_value`）+ 3 个公开步长常量（`WEIGHT_REINFORCE_DELTA` / `SUPPRESSION_RAISE_DELTA` / `REFUND_DELTA`），与「`desire/value.py`（完整）」段代码逐字一致
- [ ] `decay_value` 纯函数：线性衰减（`rate`/天）、下限 0
- [ ] `apply_pressure` / `reinforce_weight` / `raise_suppression`：各自夹到 `[0, 1]`，单调不减
- [ ] **正强化**：`reinforce_weight` 满足后表达权重**上浮**（`weight + delta`），到上限 1.0 封顶
- [ ] **习得性抑制**：`raise_suppression` 失败/抑制后抑制阈值**上浮**（`threshold + delta`），到上限 1.0 封顶
- [ ] **抑制门控**：`at_peak(value, peak_threshold)` = `value >= peak_threshold`；`is_expressible(value, suppression_threshold)` = `value >= suppression_threshold`；表达条件 = 两者同时成立（即 `value >= max(peak_threshold, suppression_threshold)`）
- [ ] `default_value(type_)` 四类型统一返回 `value=0.0`、`expression_weight=0.7`、`suppression_threshold=0.5`、`updated_at=0.0`（哨兵，由 11 初始化覆盖）
- [ ] `pyright` strict 零报错

## 技术方案

- **新文件**：`nyx/desire/value.py`（无 Facade、无 API、无数据变更、无 store）
- **库**：无新库（纯标准库）
- **公开面**：`from nyx.desire.value import (decay_value, apply_pressure, reinforce_weight, raise_suppression, at_peak, is_expressible, default_value)` + 3 个步长常量（`WEIGHT_REINFORCE_DELTA` / `SUPPRESSION_RAISE_DELTA` / `REFUND_DELTA`）；`_clamp` / 范围常量 / 初始值常量私有
- **三字段语义（design §7.2 的数值化）**：
  - `value`（压力值）∈ `[0, 1]`：活动/对话/长期欲望加压，缓慢衰减；达峰（`>= peak_threshold`，config 0.8）触发 LLM 生成短期欲望后重置为 0
  - `expression_weight`（表达权重）∈ `[0, 1]`：满足后正强化上浮（越被满足越愿表达），活动系统选消费对象时的排序权重（消费端语义归 13/17）
  - `suppression_threshold`（抑制阈值）∈ `[0, 1]`：失败/放弃/抑制后习得性抑制上浮（越挫越压抑）
- **抑制门控（决策：正强化 + 习得性抑制，已与用户确认）**：表达条件 = `at_peak(value, peak) and is_expressible(value, suppression)` = `value >= max(peak, suppression)`。初始 `suppression=0.5 < peak=0.8`，故初始表达门槛 = 0.8（peak 主导，达峰即表达）；失败每次 `+0.1`，4 次后 `suppression=0.9 > peak`，表达门槛 = 0.9（压抑主导，达峰也不表达，继续憋）。这塑造「越挫越压抑」的弧线——契合 canon 的「自卑、先怀疑自己、想太多」
- **线性衰减（决策：已与用户确认）**：`decay_value(value, elapsed_days, rate) = max(0, value - rate × elapsed_days)`，`rate` = config `desire.value_decay`（0.02 = 每天降 0.02）。签名传 `elapsed_days` 而非 `(created_at, now)`：desire_value 表已有 `updated_at` 列（04-db DDL，注释「最后一次 value 变化的时间戳」= 衰减 elapsed 来源），`elapsed_days` 由 11-desire 编排时从 `updated_at` 计算——10 只提供纯数学，不预设时间戳方案
- **回增复用 `apply_pressure`**：加压与回增数学相同（`value + delta` 夹 `[0,1]`），故合并为一个纯函数；`REFUND_DELTA=0.3` 是回增（放弃/淘汰压力回灌）的步长常量，11 调 `apply_pressure(v, REFUND_DELTA)` 表达回灌语义
- **步长与初始值（默认值，标注可推翻）**：`WEIGHT_REINFORCE_DELTA=0.05`（满足一次表达权重 +0.05）、`SUPPRESSION_RAISE_DELTA=0.1`（失败一次抑制阈值 +0.1）、`REFUND_DELTA=0.3`（回增 +0.3）；`_WEIGHT_INIT=0.7`（初始表达权重，中性偏高）、`_SUPPRESSION_INIT=0.5`（初始抑制阈值，低于 peak，初始不压抑）。四类型**统一**初始值——差异化由满足/抑制漂移自然产生，符合「从初始人设出发、由经历塑造」；若想按 canon 分类型初始，改 `default_value` 一处
- **`default_value(type_)`**：11-desire 初始化 `desire_value` 表四行时的唯一构造入口，避免初始值散落；四类型循环 `for t in DesireType: default_value(t)`
- **为什么 `at_peak` / `is_expressible` 是两个薄函数**：`peak_threshold`（config，固定）与 `suppression_threshold`（记录，动态漂移）是两个独立量，`value >= X` 的合并式 `value >= max(a,b)` 会掩盖「达峰」与「表达门槛」是两个设计概念；显式命名让抑制门控可单测、可展示（求职作品集原则 3）。两者各只有 11-evaluate 一个调用方，但语义是「值机制」对外的边界，故保留为纯函数而非内联
- **不在本 spec**：加压增量 `delta` 的具体取值（观察状态给互动欲加多少、长期欲望给对应类型加多少）由 11 按事件源编排；`value_decay` / `peak_threshold` 的实际值从 config 注入

### `desire/value.py`（完整）

```python
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
_SUPPRESSION_INIT = 0.5          # 初始抑制阈值（< peak=0.8，初始不压抑）
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


def raise_suppression(threshold: float, delta: float = SUPPRESSION_RAISE_DELTA) -> float:
    """失败/抑制后抑制阈值上浮（习得性抑制）：threshold + delta，夹到 [0, 1]。纯函数。"""
    return _clamp(threshold + delta, _SUPPRESSION_MIN, _SUPPRESSION_MAX)


def at_peak(value: float, peak_threshold: float) -> bool:
    """压力是否达峰：value >= peak_threshold。纯函数。"""
    return value >= peak_threshold


def is_expressible(value: float, suppression_threshold: float) -> bool:
    """达峰后是否可表达：value 越过抑制阈值（未被习得性抑制压住）。纯函数。"""
    return value >= suppression_threshold


def default_value(type_: DesireType) -> DesireValue:
    """某类型的初始 DesireValue：value=0、表达权重/抑制阈值用默认基线、updated_at 哨兵 0.0。纯函数。"""
    return DesireValue(
        type=type_,
        value=0.0,
        expression_weight=_WEIGHT_INIT,
        suppression_threshold=_SUPPRESSION_INIT,
        updated_at=0.0,     # 哨兵：由 11 初始化时覆盖为 now
    )
```

## 测试要点

- [ ] 单元测试 `tests/test_desire/test_value.py`（纯函数，无 DB、无 mock）：
  - [ ] `decay_value`：`elapsed_days=0` → 不变；`elapsed_days=1` → `value - rate`；衰减到负 → 夹到 0；`rate=0` → 不变
  - [ ] `apply_pressure`：加正 `delta` → 上升；加到超上限 → 夹到 1.0；`delta` 为负（防御性边界）→ 夹到下限 0.0
  - [ ] `reinforce_weight`：默认 `delta`（不传参）= `+WEIGHT_REINFORCE_DELTA`；显式 `delta` 覆盖默认；到上限 → 夹到 1.0（多次满足不越界）
  - [ ] `raise_suppression`：默认 `delta` = `+SUPPRESSION_RAISE_DELTA`；显式覆盖；到上限 → 夹到 1.0
  - [ ] `at_peak`：`value > threshold` → `True`；`value < threshold` → `False`；`value == threshold` → `True`（含等号）
  - [ ] `is_expressible`：同 `at_peak` 边界（用 `suppression_threshold`）
  - [ ] **门控组合（回归：习得性抑制）**：初始 `suppression=0.5`、`peak=0.8`、`value=0.8` → `at_peak` 且 `is_expressible`（表达）；失败 4 次后 `suppression=0.9`、`value=0.8` → `at_peak` 但**不** `is_expressible`（被压抑）
  - [ ] `default_value`：四个 `DesireType` 各自返回 `value == 0.0`、`expression_weight == 0.7`、`suppression_threshold == 0.5`、`updated_at == 0.0`、`type` 正确
  - [ ] 常量断言：`0.0 <= WEIGHT_REINFORCE_DELTA <= SUPPRESSION_RAISE_DELTA`、`REFUND_DELTA > 0`
- [ ] 集成测试：无（纯函数，无管道；与 `DesireFacade` 的编排归 11）
- [ ] E2E 测试：无

## 完成定义

- [ ] `ruff check` 零报错
- [ ] `pyright` 零报错
- [ ] `pytest` 全绿
- [ ] `test-inventory.md` 已更新
- [ ] 11-desire 的 `DesireFacade` 编排时调 `value.py` 纯函数（不重写衰减/夹取逻辑）；初始化 `desire_value` 四行用 `default_value(t)`；`value_decay` / `peak_threshold` 从 config 注入、`elapsed_days` 来源由 11 定（04-db `desire_value.updated_at` 已落地，见 04-db DDL）
