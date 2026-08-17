# 日程排期（scheduler）

> 范围：`activity/scheduler.py`（时间格 grid → 活动排期）。纯函数：无 IO、无 DB、无 LLM、无 Facade。
> 纯基础设施 spec：只做「欲望→活动映射 + 消费排序 + 精力约束排期 + 时间标签格式化」四件事，不含 store、不含 Facade、不含 LLM、不含活动生命周期编排（归 14-activity）。
> **本文件自包含**：`activity/scheduler.py` 完整代码内联在下文。

## 元信息

- **前置依赖**：01-types（`ActivityType` / `DesireType` / `ShortTermDesire` / `DesireValue`）、02-config（`ActivityEnergyDelta`——仅作 `build_schedule` 参数的类型注解，不 import 配置加载逻辑）、12-inner-life（`emotion.ENERGY_REST_THRESHOLD`——精力休息阈值单一来源，不 import Facade）

## 用户故事

> 作为 Nyx 系统的开发者，我想要日程排期的纯函数层——欲望→活动映射、消费排序、精力约束的排期、时间标签格式化，以便 14-activity 的 `ActivityFacade` 编排时调这些纯函数、前端/评审能直接读懂「尼克斯今天为什么先读书、精力低了为什么先休息」。

## 验收标准

- [ ] `scheduler.py` 含 4 个公开纯函数（`desire_to_activity` / `rank_desires` / `build_schedule` / `format_time_label`），与「`activity/scheduler.py`（完整）」段代码逐字一致
- [ ] `desire_to_activity`：`EXPLORATION→READING`、`CREATION→CREATION`、`REST→REST`、`INTERACTION→None`（互动欲不占日程块）
- [ ] `rank_desires`：按类型级 `expression_weight` 降序、同权 `created_at` 升序（FIFO 稳定）；无 `DesireValue` 记录的类型按 0.0 兜底
- [ ] `build_schedule`：欲望序列 → 活动序列；精力跌破 `ENERGY_REST_THRESHOLD` 时在下一活动前插 `REST` 恢复；保持输入顺序；`energy_delta.rest <= 0` 时不进死循环
- [ ] `format_time_label`：块序号 → `"HH:MM"` 时间标签
- [ ] `pyright` strict 零报错

## 技术方案

- **新文件**：`nyx/activity/scheduler.py`（无 Facade、无 API、无数据变更、无 store）
- **库**：无新库（纯标准库）
- **公开面**：`from nyx.activity.scheduler import (desire_to_activity, rank_desires, build_schedule, format_time_label)`（休息阈值 `ENERGY_REST_THRESHOLD` 从 `nyx.inner_life.emotion` 共享导入，非 scheduler 私有）
- **为什么是纯函数、不建表**：design §8.1「日程块是 grid 派生的临时概念，不建表持久化」——13 只产「活动类型序列」的排期数学，`Activity` 记录（含 `schedule_block_id` 时间标签）由 14 构造后落 `activity` 表
- **欲望→活动映射（`desire_to_activity`）**：六种日程块活动里，欲望驱动的只有 3 种（design §8.2）——探索欲→读书（自由探索是读书的**升级形态**，触发门槛「探索欲峰值 + 精力充足 + 频率上限」见 design §8.6，归 14 运行时判定后把 `READING` 覆盖成 `FREE_EXPLORATION`）；创造欲→创作；休息欲→休息。**互动欲不占日程块**（走搭话/对话，见 §5.5），返回 `None`。观察用户/发呆反思**不在本 spec**——非欲望驱动，是 14 的「空槽默认」行为，不进 `build_schedule` 输出
- **消费排序（`rank_desires`）**：10-desire-value §33 约定「消费对象排序权重 = 类型级 `expression_weight`，消费端语义归 13/17」。输入 desires 已由 11 `get_pending()` 过滤为 `pending/active`（`created_at ASC` FIFO），本函数再按 `expression_weight` 降序重排（越愿表达越先消费）、同权 `created_at` 升序（FIFO 稳定）；`DesireValue` 缺失类型按 0.0 兜底（纯函数防御，正常 18-api 已 seed 四类型）
- **精力符号约定（显式声明）**：`energy_delta` 字段符号——`reading=-20` / `creation=-25` 为负=消耗、`rest=+30` 为正=恢复、`idle_reflection=+10` 为正=微恢复（design §8.4；默认值见 02-config `ActivityEnergyDelta`）。`build_schedule` 只消费 `reading` / `creation` / `rest` 三字段；`observe_user` / `idle_reflection` / `free_exploration` 由 14 消费
- **精力约束（`build_schedule`）**：阈值 `ENERGY_REST_THRESHOLD = 40.0`（单一来源，定义在 `inner_life/emotion.py`，即 `energy_to_state` 的 TIRED 档下界）——精力跌破此值进入力竭/枯竭档（EXHAUSTED/DRAINED），下一活动前先插 `REST`（+30 恢复）；精力极低（如 0）时连续多个 `REST` 直到恢复。**防死循环**：`energy_delta.rest <= 0` 时跳过休息插值（正常 config `rest=30>0`，02-config 只校验 int 不校验正负，纯函数自行防御）
- **时间标签（`format_time_label`）**：`block_index` 块起点 = `start_hour + block_index * grid_minutes / 60`，格式 `"HH:MM"`；`start_hour` 是「排期起点的当日小时数（浮点，如 9.5 = 9:30）」，由 14 在运行时定（服务启动时刻/当前时刻/当天零点）
- **不引入 `ScheduleBlock` 中间类型**：`build_schedule` 输出裸 `list[ActivityType]`，时间标签由 `format_time_label` 单独算，14 用 `enumerate` 组合成 `Activity`——中间结构只有 14 一个消费方，内联即可（反冗余）
- **`ActivityEnergyDelta` 的 import**：仅作 `build_schedule` 参数类型注解（`from nyx.config import ActivityEnergyDelta`），不是「import 配置加载逻辑」——14 从 `config.activity.energy_delta` 取出来传参，13 不读配置文件、不碰 `load_config`

### `activity/scheduler.py`（完整）

```python
"""日程排期纯函数：欲望队列 + 精力 → 一天活动序列。

无 IO、无 DB、无 LLM、无 Facade。14-activity 的 ActivityFacade 消费这些纯函数。
"""
from nyx.config import ActivityEnergyDelta
from nyx.enums import ActivityType, DesireType
from nyx.inner_life.emotion import ENERGY_REST_THRESHOLD
from nyx.types import DesireValue, ShortTermDesire


def desire_to_activity(desire_type: DesireType) -> ActivityType | None:
    """欲望类型 → 日程块活动类型。

    探索欲→读书（自由探索是读书的升级形态，由 14 运行时判定）；创造欲→创作；
    休息欲→休息；互动欲不占日程块（走搭话/对话），返回 None。
    """
    if desire_type is DesireType.EXPLORATION:
        return ActivityType.READING
    if desire_type is DesireType.CREATION:
        return ActivityType.CREATION
    if desire_type is DesireType.REST:
        return ActivityType.REST
    return None


def rank_desires(
    desires: list[ShortTermDesire],
    values: list[DesireValue],
) -> list[ShortTermDesire]:
    """消费排序：类型级表达权重降序（越愿表达越先消费），同权按 created_at 升序
    （FIFO 稳定）。

    expression_weight 缺省（该类型无 DesireValue 记录）按 0.0 处理。
    """
    weight = {v.type: v.expression_weight for v in values}
    return sorted(desires, key=lambda d: (-weight.get(d.type, 0.0), d.created_at))


def build_schedule(
    desires: list[ShortTermDesire],
    energy: float,
    energy_delta: ActivityEnergyDelta,
) -> list[ActivityType]:
    """欲望序列 → 一天活动序列（含精力驱动的休息穿插）。

    契约：调用方先 rank_desires 排序（本函数保持输入顺序，不自行排序）。
    精力模拟：逐块累加 energy_delta；精力跌破 ENERGY_REST_THRESHOLD 时，
    在下一活动前插 REST 块恢复，直到回到阈值之上；energy_delta.rest <= 0 时
    跳过（防死循环）。
    """
    result: list[ActivityType] = []
    cur = energy
    for desire in desires:
        activity = desire_to_activity(desire.type)
        if activity is None:
            continue
        while cur < ENERGY_REST_THRESHOLD and energy_delta.rest > 0:
            result.append(ActivityType.REST)
            cur += energy_delta.rest
        result.append(activity)
        cur += getattr(energy_delta, activity.value)
    return result


def format_time_label(block_index: int, grid_minutes: int, start_hour: float) -> str:
    """块序号 → "HH:MM" 时间标签（14 构造 Activity.schedule_block_id 用）。

    第 block_index 块起点 = start_hour + block_index * grid_minutes / 60。
    """
    minutes = round(start_hour * 60 + block_index * grid_minutes)
    return f"{minutes // 60:02d}:{minutes % 60:02d}"
```

## 测试要点

- [ ] 单元测试 `tests/test_activity/test_scheduler.py`（纯函数，无 DB、无 mock、无 async）：
  - [ ] `desire_to_activity`：四种 `DesireType` 映射穷尽——`EXPLORATION→READING`、`CREATION→CREATION`、`REST→REST`、`INTERACTION→None`
  - [ ] `rank_desires`：造三种 `type` 的欲望 + 对应 `DesireValue`（`expression_weight` 高低不一）→ 按权重降序；同权两条 → 按 `created_at` 升序（稳定）；某 `type` 无 `DesireValue` → 该条按 0.0 排最后；空列表 → `[]`
  - [ ] `build_schedule`：
    - [ ] 空 desires → `[]`（不排空块）
    - [ ] 精力充足（如 100）多条探索/创造/休息欲 → 按输入顺序产出对应活动、不插休息
    - [ ] 精力不足（如 30）一条探索欲 → 前面先插 `REST`（`[REST, READING]`）
    - [ ] 精力极低（如 0）→ 连续多个 `REST` 直到恢复（`[REST, REST, READING]`，`rest=+30` 时 0→30→60）
    - [ ] 含互动欲 → 被跳过（`continue`），不产块、不耗精力
    - [ ] `energy_delta.rest <= 0`（用 0 代表；`> 0` 为 False 跳过同一条路径，`rest<0` 行为一致）→ 不死循环，直接产出活动块
    - [ ] 保持输入顺序：`[探索, 创造]` 且精力足 → `[READING, CREATION]`（不自行排序）
  - [ ] `format_time_label`：`(0, 60, 9.0)→"09:00"`；`(1, 60, 9.0)→"10:00"`；`(2, 60, 9.5)→"11:30"`；`(0, 30, 0.0)→"00:00"`（半小时一块）；浮点小时 `4.1/8.2/16.4/16.9` → `"04:06"/"08:12"/"16:24"/"16:54"`（`round` 不截断少一分钟）
  - [ ] 常量断言：`0.0 <= ENERGY_REST_THRESHOLD <= 100.0`（从 `nyx.inner_life.emotion` 导入）
- [ ] 集成测试：无（纯函数，无管道；与 `ActivityFacade` 的编排归 14）
- [ ] E2E 测试：无

## 完成定义

- [ ] `ruff check` 零报错
- [ ] `pyright` 零报错
- [ ] `pytest` 全绿
- [ ] `test-inventory.md` 已更新
- [ ] 14-activity 的 `ActivityFacade` 编排时调 `scheduler.py` 纯函数（`select_activity` 用 `rank_desires` 排序、`get_schedule`/`select_activity` 用 `build_schedule` + `format_time_label` 组合成 `Activity`）；`energy_delta` 从 config 注入、`start_hour` 由 14 定
