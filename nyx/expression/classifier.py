"""表达分类纯函数：快慢通道、问句和轻量用户意图。"""

import re

from nyx.enums import ContextMode, UserIntent
from nyx.types import CurrentState

# 消息长度归一化：≥50 字符视为长消息（可推翻）
_LONG_MSG_LEN = 50.0
# 距上次慢通道归一化：≥3600 秒（1 小时）视为满（可推翻）
_RECENCY_WINDOW = 3600.0
QUESTION_MARKS = ("?", "？", "吗", "呢", "怎么", "为什么", "什么", "如何", "哪")
_QUESTION_WORDS = (
    "怎么", "怎么样", "为什么", "什么", "如何", "哪", "是否", "能否", "可否",
)
_REQUEST_WORDS = ("请", "帮我", "告诉我", "解释", "推荐", "写一份", "怎么做")
_EMOTIONAL_WORDS = (
    "难过", "伤心", "生气", "愤怒", "开心", "高兴", "焦虑", "担心",
    "害怕", "委屈", "烦躁", "疲惫", "孤独", "崩溃", "压力",
)
_GREETING_RE = re.compile(
    r"^(你好|嗨|hi|hello|早上好|晚上好|晚安|在吗)[！!。.,， ]*$",
    re.I,
)
_EMOTION_WORDS = (
    "难过", "伤心", "生气", "愤怒", "开心", "高兴", "焦虑", "担心",
    "害怕", "委屈", "烦躁", "疲惫", "孤独",
)


def is_question(text: str) -> bool:
    """Return whether text is syntactically asking a question."""
    normalized = text.strip()
    if not normalized:
        return False
    if "?" in normalized or "？" in normalized:
        return True
    parts = re.split(r"[。！？!?；;\n]", normalized)
    for part in parts:
        clause = part.strip()
        if not clause:
            continue
        if clause.endswith(("吗", "呢", "吧", "是否", "能否", "可否")):
            return True
        if clause.startswith(_QUESTION_WORDS):
            return True
        if clause.endswith(("怎么样", "如何", "什么", "哪里", "哪儿")):
            return True
        if any(
            clause.startswith(prefix)
            for prefix in ("你觉得", "你认为", "有没有", "是不是", "可不可以")
        ):
            return True
    return False


def classify_user_intent(text: str) -> UserIntent:
    """Classify user intent without LLM or I/O; only a prompt hint."""
    normalized = " ".join(text.strip().split())
    if not normalized:
        return UserIntent.UNKNOWN
    if is_question(normalized):
        return UserIntent.QUESTION
    if any(word in normalized for word in _REQUEST_WORDS):
        return UserIntent.REQUEST
    if any(word in normalized for word in _EMOTIONAL_WORDS):
        return UserIntent.EMOTIONAL_SUPPORT
    if _GREETING_RE.fullmatch(normalized):
        return UserIntent.GREETING
    if len(normalized) >= 2:
        return UserIntent.SHARING
    return UserIntent.UNKNOWN


def slow_score(
    message: str, state: CurrentState, now: float, last_slow_at: float
) -> float:
    """慢通道倾向得分 0-1，越高越该走慢通道（契约见 11-expression spec）。

    5 因子（权重和=1）：消息长度 0.25 + 含问句 0.25 + 情感词 0.20
    + 精力/情感 0.15 + 距上次慢通道 0.15。
    「精力/情感」= 精力足且情绪平静 → 倾向慢（有力气深聊）；精力低或激动 → 倾向快。
    """
    length = min(1.0, len(message) / _LONG_MSG_LEN)
    question = 1.0 if is_question(message) else 0.0
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
