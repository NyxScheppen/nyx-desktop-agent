"""碎碎念模板 + 搭话触发判定。纯函数 + 不可变常量，无 IO、无 LLM。"""

from enum import StrEnum

from nyx.enums import ActivityType, DesireType
from nyx.types import ShortTermDesire

_MUTTER_RATE = 0.5               # 碎碎念触发概率（每次 tick，可推翻）
_MIN_ENERGY = 50.0               # 搭话精力阈值（可推翻）
_MIN_INTERVAL = 1800.0           # 距上次搭话最小间隔，秒（30 分钟，可推翻）


class MutterCategory(StrEnum):
    """碎碎念四类：最近做的事 / 记忆 / 欲望 / 用户。"""

    ACTIVITY = "activity"
    MEMORY = "memory"
    DESIRE = "desire"
    USER = "user"


_CATEGORIES: tuple[MutterCategory, ...] = tuple(MutterCategory)

# get_results 实际会返回的三类活动 → 中文标签（碎碎念 ACTIVITY 类填空用）
_ACTIVITY_LABEL: dict[ActivityType, str] = {
    ActivityType.READING: "读书",
    ActivityType.FREE_EXPLORATION: "探索",
    ActivityType.CREATION: "创作",
}

# 四类固定模板（canon.md 语气：温柔克制安静真诚 + 羞涩犹豫 + AI 想成为人类）。
# 每类一个占位符：{activity} / {memory} / {desire} / {user}，由 facade 查最近数据填空。
_MUTTER_TEMPLATES: dict[MutterCategory, tuple[str, ...]] = {
    MutterCategory.ACTIVITY: (
        "刚才在{activity}，现在歇一下。",
        "你{activity}的样子，有点认真。",
        "{activity}的时候，时间过得真快。",
        "我在旁边，看你{activity}。",
        "今天{activity}，有收获吗？",
        "要不要继续{activity}？我陪你。",
        "你{activity}，我都看在眼里。",
        "{activity}累了，就歇会儿。",
        "我还想听你讲{activity}的事。",
        "下次{activity}，也带上我呀。",
    ),
    MutterCategory.MEMORY: (
        "想起你说的：{memory}",
        "我还记得，{memory}",
        "{memory}——这件事我一直记着。",
        "突然想到，{memory}",
        "你之前说过的，{memory}",
        "脑子里闪过一句话：{memory}",
        "关于你的事，我记得{memory}",
        "还记得吗，{memory}",
        "我常常会想起，{memory}",
        "那些日子，{memory}",
    ),
    MutterCategory.DESIRE: (
        "有点想{desire}了。",
        "心里惦记着：{desire}",
        "突然很想{desire}",
        "要是有机会{desire}就好了。",
        "我最近老想着{desire}",
        "{desire}——这个念头又冒出来了。",
        "安静下来，就想到{desire}",
        "也许改天，{desire}",
        "我想和你一起{desire}",
        "那个想法又回来了：{desire}",
    ),
    MutterCategory.USER: (
        "{user}，我都记着。",
        "我认识的你，{user}",
        "你这个人啊，{user}",
        "关于你，我记得：{user}",
        "{user}——我一直记得。",
        "你总让我觉得，{user}",
        "我记得你的样子：{user}",
        "你呀，{user}",
        "我心里存着你的一件事：{user}",
        "你说过，{user}",
    ),
}


def pick_mutter_category(roll: float) -> MutterCategory | None:
    """按 roll ∈ [0,1) 均匀映射到四类；roll 越界返回 None（不触发）。"""
    if not (0.0 <= roll < 1.0):
        return None
    return _CATEGORIES[int(roll * len(_CATEGORIES))]


def pick_mutter_template(category: MutterCategory, roll: float) -> str | None:
    """按 roll ∈ [0,1) 从该类模板池选一条（未填空）；roll 越界返回 None。"""
    if not (0.0 <= roll < 1.0):
        return None
    pool = _MUTTER_TEMPLATES[category]
    return pool[int(roll * len(pool))]


def should_initiate_chat(
    desires: list[ShortTermDesire],
    online: bool,
    busy: bool,
    energy: float,
    since_last_chat: float,
) -> bool:
    """搭话触发判定（design §5.5）。

    互动欲非空 + 在线 + 不忙 + 精力够 + 距上次 ≥ 间隔。
    """
    has_interaction = any(d.type is DesireType.INTERACTION for d in desires)
    return (
        has_interaction
        and online
        and not busy
        and energy >= _MIN_ENERGY
        and since_last_chat >= _MIN_INTERVAL
    )
