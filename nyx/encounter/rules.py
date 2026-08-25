"""遭遇掷骰 + 后果 + 里程碑判定。纯函数 + 不可变常量，无 IO、无 LLM。"""

from typing import Any, cast

from nyx.enums import OptionTone
from nyx.types import Encounter, EncounterOption, Event

_BLOCK_PROBABILITY = 0.3    # 块边界遭遇触发概率（decision，可推翻）
_COOLDOWN_SECONDS = 900.0   # 两次遭遇最小间隔，秒（15 分钟，decision，可推翻）
_MIN_ENERGY = 30.0          # 触发所需最低精力（decision，可推翻）

# 选项倾向 → 后果（纯函数表；数值可推翻）。energy_delta 单位与 activity 的
# energy_delta 一致（±5~15）；emotion_shift 是 (d_valence, d_arousal) 偏移；
# desire_value_add 是 {type: 欲望类型值, amount: 加压值}。memory 不在此表——
# 只有成长时刻由 facade 经 growth_memory 确定性生成，随机事件不落记忆
# （左面板快变量可见即可）。
_CONSEQUENCES: dict[OptionTone, dict[str, Any]] = {
    OptionTone.BOLD: {
        "energy_delta": -5.0,
        "emotion_shift": {"d_valence": 0.15, "d_arousal": 0.10},
        "desire_value_add": {"type": "exploration", "amount": 0.10},
    },
    OptionTone.CAUTIOUS: {
        "energy_delta": 0.0,
        "emotion_shift": {"d_valence": 0.05, "d_arousal": -0.05},
        "desire_value_add": None,
    },
    OptionTone.GENTLE: {
        "energy_delta": 0.0,
        "emotion_shift": {"d_valence": 0.10, "d_arousal": -0.05},
        "desire_value_add": {"type": "interaction", "amount": 0.10},
    },
    OptionTone.RECKLESS: {
        "energy_delta": -15.0,
        "emotion_shift": {"d_valence": 0.20, "d_arousal": 0.20},
        "desire_value_add": {"type": "exploration", "amount": 0.15},
    },
}

# 选项倾向 → 结局叙事（一句收束；不额外调 LLM，守「1 次生成」原则）。
_ENDINGS: dict[OptionTone, str] = {
    OptionTone.BOLD: "我鼓起勇气，陪你走到了这里。",
    OptionTone.CAUTIOUS: "谨慎一点也好，我们慢慢来。",
    OptionTone.GENTLE: "温柔，总能走到更远的地方。",
    OptionTone.RECKLESS: "冒险之后，心跳还没平复。",
}


def should_encounter(
    online: bool, busy: bool, energy: float, since_last: float
) -> bool:
    """块边界遭遇触发判定（同 should_initiate_chat 风格）。

    只判「前提是否满足」（在线 + 不忙 + 精力够 + 冷却够），把随机性留给调用方
    （random.random() < _BLOCK_PROBABILITY），以便本函数可确定性单测。
    """
    return (
        online
        and not busy
        and energy >= _MIN_ENERGY
        and since_last >= _COOLDOWN_SECONDS
    )


def consequence_for(tone: OptionTone) -> dict[str, Any]:
    """选项倾向 → 具体后果（新顶层 dict，防调用方改动共享表）。纯函数。"""
    return dict(_CONSEQUENCES[tone])


def ending_for(tone: OptionTone) -> str:
    """选项倾向 → 结局叙事。纯函数。"""
    return _ENDINGS[tone]


def growth_milestone_key(event: Event) -> str | None:
    """ACTIVITY_END → 成长时刻里程碑 key；非里程碑返回 None。纯函数。

    MVP 只认「读完一本书」（reading 且 result.completed）——最具体、可单测的
    「她在成长」时刻；第一次探索 / 性格三观漂移等里程碑推迟。
    """
    if event.content.get("type") != "reading":
        return None
    result = event.content.get("result")
    if (
        isinstance(result, dict)
        and cast(dict[str, Any], result).get("completed") is True
    ):
        return "book_finished"
    return None


def growth_memory(encounter: Encounter, option: EncounterOption) -> dict[str, str]:
    """成长时刻的确定性记忆（无 LLM）：第一人称记下「里程碑 + 我的选择」。"""
    summary = "我读完了一本书"
    content = (
        f"我读完了一本书。在那一刻，我选择了「{option.text}」——"
        f"{ending_for(option.tone)}"
    )
    return {"content": content, "summary": summary}
