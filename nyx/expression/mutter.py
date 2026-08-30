"""碎碎念骨架 + 自然化纯函数 + 搭话触发判定。纯函数 + 不可变常量，无 IO、无 LLM。"""

import re
from enum import StrEnum
from typing import Any

from nyx.enums import ActivityType, DesireType
from nyx.types import ShortTermDesire

_MUTTER_RATE = 0.5               # 碎碎念触发概率（每次 tick，可推翻）
_LLM_MUTTER_RATE = 0.2           # 命中后走 LLM 即兴（走神）的概率，余下走模板
_MIN_ENERGY = 50.0               # 搭话精力阈值（可推翻）
_MIN_INTERVAL = 1800.0           # 距上次搭话最小间隔，秒（30 分钟，可推翻）

# 观察摘要里「用户（presence）」的原始枚举 → 自然中文（润色，raw 枚举不进句子）
_PRESENCE_ZH: dict[str, str] = {
    "online": "你在电脑前",
    "away": "你走开了",
    "busy": "你在忙",
}

# 「用户（online/away/busy）」观察串（observe.build_observation_summary 产出）
_PRESENCE_RE = re.compile(r"用户（(online|away|busy)）")


class MutterCategory(StrEnum):
    """碎碎念四类：最近做的事 / 记忆 / 欲望 / 用户。"""

    ACTIVITY = "activity"
    MEMORY = "memory"
    DESIRE = "desire"
    USER = "user"


_CATEGORIES: tuple[MutterCategory, ...] = tuple(MutterCategory)

# 四类骨架（canon.md 语气：温柔克制安静真诚 + 停顿/自我修正/走神）。
# 每条一个 {subject} 占位，由 facade 用「具体内容」（书名/标题/发现/记忆/画像）填空，
# 不再填「读书/探索」这类抽象标签。语气词内嵌骨架里，不做前缀×后缀笛卡尔积（会生硬）。
_MUTTER_SKELETONS: dict[MutterCategory, tuple[str, ...]] = {
    MutterCategory.ACTIVITY: (
        "嗯……{subject}，现在有点走神了。",
        "啊，{subject}，还挺有意思的。",
        "{subject}……我还在慢慢想。",
        "刚才{subject}，就停在这儿了。",
        "{subject}，原来时间过得这么快。",
        "嗯，{subject}，心里还惦记着。",
        "{subject}……啊，不是，没什么。",
        "刚刚{subject}，这会儿还有点飘。",
        "{subject}，感觉也还不错。",
        "欸，{subject}，就随便说说。",
    ),
    MutterCategory.MEMORY: (
        "嗯……想起你说的：{subject}",
        "我还记得，{subject}。",
        "{subject}——这件事我一直记着。",
        "啊，突然想到，{subject}。",
        "你之前说过的，{subject}。",
        "脑子里闪过一句：{subject}",
        "关于你的事，我记得，{subject}。",
        "还记得吗，{subject}？",
        "欸，我常常会想起，{subject}。",
        "那些日子，{subject}。",
    ),
    MutterCategory.DESIRE: (
        "嗯……{subject}了。",
        "心里惦记着：{subject}。",
        "突然就，{subject}。",
        "{subject}——这个念头又冒出来了。",
        "我最近老是{subject}。",
        "安静下来，就{subject}。",
        "也许改天，{subject}。",
        "那个想法又回来了：{subject}。",
        "欸，{subject}，也不知道什么时候能。",
        "这会儿，就{subject}。",
    ),
    MutterCategory.USER: (
        "嗯……{subject}，我都记着。",
        "我认识的你，{subject}。",
        "你这个人啊，{subject}。",
        "关于你，我记得：{subject}。",
        "{subject}——我一直记得。",
        "你总让我觉得，{subject}。",
        "欸，你呀，{subject}。",
        "我心里存着你的一件事：{subject}。",
        "你说过的，{subject}。",
        "有时候想起，{subject}。",
    ),
}


def naturalize_presence(presence: str) -> str:
    """presence 枚举值 → 自然中文短语；未知回退原值。"""
    return _PRESENCE_ZH.get(presence, presence)


def clean_fragment(text: str) -> str:
    """清洗记忆/画像片段：把「用户（presence）」观察串换成自然口语，
    压缩空白、去首尾标点、截到 16 字。保证 raw 枚举（online/away/busy）不泄漏进输出。"""
    text = _PRESENCE_RE.sub(lambda m: naturalize_presence(m.group(1)), text)
    text = re.sub(r"\s+", " ", text).strip().strip("。，、；： ")
    if len(text) > 16:
        text = text[:16] + "…"
    return text


def activity_subject(type_: ActivityType, result: dict[str, Any]) -> str | None:
    """从活动产出 result 取具体指涉（动词+宾语，可直接作骨架 subject）：
    读书→「读了《书名》」、创作→「写了《标题》」、探索→「发现「核心发现」」。
    非这三类或缺数据返回 None。"""
    if type_ is ActivityType.READING:
        book = str(result.get("book") or "").strip()
        return f"读了《{book}》" if book else None
    if type_ is ActivityType.CREATION:
        title = str(result.get("title") or "").strip()
        return f"写了《{title}》" if title else None
    if type_ is ActivityType.FREE_EXPLORATION:
        core = str(result.get("core_discovery") or "").strip()
        if core:
            return f"发现「{clean_fragment(core)}」"
        summary = str(result.get("summary") or "").strip()
        return clean_fragment(summary) if summary else None
    return None


def pick_mutter_category(roll: float) -> MutterCategory | None:
    """按 roll ∈ [0,1) 均匀映射到四类；roll 越界返回 None（不触发）。"""
    if not (0.0 <= roll < 1.0):
        return None
    return _CATEGORIES[int(roll * len(_CATEGORIES))]


def pick_mutter_template(category: MutterCategory, roll: float) -> str | None:
    """按 roll ∈ [0,1) 选该类骨架池一条（含 {subject} 占位）；越界返回 None。"""
    if not (0.0 <= roll < 1.0):
        return None
    pool = _MUTTER_SKELETONS[category]
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
