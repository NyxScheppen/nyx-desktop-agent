"""日程排期纯函数：欲望队列 + 精力 → 一天活动序列。

无 IO、无 DB、无 LLM、无 Facade。14-activity 的 ActivityFacade 消费这些纯函数。
"""
from nyx.config import ActivityEnergyDelta
from nyx.enums import ActivityType, DesireType
from nyx.types import DesireValue, ShortTermDesire

# —— 精力恢复阈值：精力跌破此值（进入「疲惫」档）时，下一活动前先插休息块 ——
_REST_ENERGY_THRESHOLD = 40.0   # 默认值，可推翻


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
    精力模拟：逐块累加 energy_delta；精力跌破 _REST_ENERGY_THRESHOLD 时，
    在下一活动前插 REST 块恢复，直到回到阈值之上；energy_delta.rest <= 0 时
    跳过（防死循环）。
    """
    result: list[ActivityType] = []
    cur = energy
    for desire in desires:
        activity = desire_to_activity(desire.type)
        if activity is None:
            continue
        while cur < _REST_ENERGY_THRESHOLD and energy_delta.rest > 0:
            result.append(ActivityType.REST)
            cur += energy_delta.rest
        result.append(activity)
        cur += _delta_of(activity, energy_delta)
    return result


def _delta_of(activity: ActivityType, energy_delta: ActivityEnergyDelta) -> float:
    """活动类型 → 精力消耗。只处理 build_schedule 产出的三种。

    入参范围由 desire_to_activity 保证只有 READING/CREATION/REST；下方 raise 是二道保险
    （正常不可达）——若未来映射新增活动类型而忘补 delta，这里 fail-fast，而非静默按
    rest 处理（静默会掩盖「新活动没记精力」的 bug）。
    """
    if activity is ActivityType.READING:
        return energy_delta.reading
    if activity is ActivityType.CREATION:
        return energy_delta.creation
    if activity is ActivityType.REST:
        return energy_delta.rest
    raise ValueError(f"不在日程排期内的活动类型：{activity}")


def format_time_label(block_index: int, grid_minutes: int, start_hour: float) -> str:
    """块序号 → "HH:MM" 时间标签（14 构造 Activity.schedule_block_id 用）。

    第 block_index 块起点 = start_hour + block_index * grid_minutes / 60。
    """
    minutes = int(start_hour * 60 + block_index * grid_minutes)
    return f"{minutes // 60:02d}:{minutes % 60:02d}"
