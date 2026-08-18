"""快慢通道判定：5 因子加权 → 0-1，与 slow_threshold 比较。纯函数。"""

from nyx.enums import ContextMode
from nyx.types import CurrentState

# 消息长度归一化：≥50 字符视为长消息（可推翻）
_LONG_MSG_LEN = 50.0
# 距上次慢通道归一化：≥3600 秒（1 小时）视为满（可推翻）
_RECENCY_WINDOW = 3600.0
QUESTION_MARKS = ("?", "？", "吗", "呢", "怎么", "为什么", "什么", "如何", "哪")
_EMOTION_WORDS = (
    "难过", "伤心", "生气", "愤怒", "开心", "高兴", "焦虑", "担心",
    "害怕", "委屈", "烦", "累", "孤独",
)


def slow_score(
    message: str, state: CurrentState, now: float, last_slow_at: float
) -> float:
    """慢通道倾向得分 0-1，越高越该走慢通道（design §5.2）。

    5 因子（权重和=1）：消息长度 0.25 + 含问句 0.25 + 情感词 0.20
    + 精力/情感 0.15 + 距上次慢通道 0.15。
    「精力/情感」= 精力足且情绪平静 → 倾向慢（有力气深聊）；精力低或激动 → 倾向快。
    """
    length = min(1.0, len(message) / _LONG_MSG_LEN)
    question = 1.0 if any(m in message for m in QUESTION_MARKS) else 0.0
    emotion = 1.0 if any(w in message for w in _EMOTION_WORDS) else 0.0
    # 不夹：energy/arousal 已在上游 clamp 到 [0,100]/[0,1]
    vigor = 0.5 * (state.energy / 100.0) + 0.5 * (1.0 - state.arousal)
    # 上下限都夹：last_slow_at>now（时钟回拨）也不为负
    recency = max(0.0, min(1.0, (now - last_slow_at) / _RECENCY_WINDOW))
    return (
        0.25 * length + 0.25 * question + 0.20 * emotion
        + 0.15 * vigor + 0.15 * recency
    )


def classify_channel(
    message: str,
    state: CurrentState,
    now: float,
    last_slow_at: float,
    threshold: float,
) -> ContextMode:
    """判定快/慢通道：slow_score ≥ threshold → 慢，否则快。"""
    return (
        ContextMode.SLOW
        if slow_score(message, state, now, last_slow_at) >= threshold
        else ContextMode.FAST
    )
