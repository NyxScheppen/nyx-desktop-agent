"""表达分类纯函数：快慢通道、问句和轻量用户意图。"""

import re
from collections.abc import Sequence

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
_DISCUSSION_TERMS = (
    "为什么", "如何", "怎么", "背景", "辨析", "比较", "观点", "历史",
    "原理", "定义", "什么", "哪",
)
_DISCUSSION_REQUEST_RE = re.compile(
    r"^(?:请|帮我|给我|解释|说明|分析|比较|辨析|介绍|讲讲|谈谈|说说|"
    r"告诉我|梳理|评价|解读|阐述)"
)
_DISCUSSION_OPEN_RE = re.compile(
    r"(?:讨论|聊聊)(?:一下|下)?[\s：:，,]*"
)
_DISCUSSION_NEGATION_RE = re.compile(
    r"(?:不(?:(?:太|怎么|是很)?(?:需要|打算|准备|愿意|想|要|用|必|希望))?"
    r"(?:再)?(?:讨论|聊聊)"
    r"|无需(?:再)?(?:讨论|聊聊)|没必要(?:再)?(?:讨论|聊聊)"
    r"|别(?:再)?(?:讨论|聊聊)|(?:禁止|拒绝)(?:再)?(?:讨论|聊聊))"
)
_CASUAL_QUESTION_RE = re.compile(
    r"(?:怎么样|怎么了|怎么啦|咋样|咋了|好吗|还好吗)[？?！!。]*$"
)
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


def derive_request_text(messages: Sequence[str]) -> tuple[int, str]:
    """Only an exact first control clause replaces the whole request."""
    controls = {
        "取消整个任务",
        "撤回整个任务",
        "替换整个任务",
        "取消刚才的任务",
        "撤回刚才的任务",
        "取消前面的请求",
        "撤回前面的请求",
        "换个话题",
    }
    start = 0
    for index, message in enumerate(messages):
        first = next(
            (s.strip() for s in re.split(r"[，,。.!！?？；;\n]", message) if s.strip()),
            "",
        )
        if first in controls:
            start = index
    return start, "\n\n".join(messages[start:])


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


def should_load_discussion(message: str) -> bool:
    """Return whether a message is an explicit knowledge/discussion request.

    This is deliberately conservative: discussion guidance is only added when
    a discussion cue is paired with a question or an imperative request.
    """
    normalized = " ".join(message.strip().split())
    if not normalized:
        return False
    for clause in re.split(r"[，,。！？!?；;\n]", normalized):
        if _DISCUSSION_NEGATION_RE.search(clause):
            continue
        opening = _DISCUSSION_OPEN_RE.search(clause)
        if opening and clause[opening.end():].strip(
            " \t\r\n：:，,、。！？!?；;吗呢吧呀哦嘛啊啦好行可以不"
        ):
            return True
        if _CASUAL_QUESTION_RE.search(clause) and not any(
            term in clause for term in _DISCUSSION_TERMS if term != "怎么"
        ):
            continue
        if not any(term in clause for term in _DISCUSSION_TERMS):
            continue
        has_embedded_question_word = any(
            word in clause for word in _QUESTION_WORDS
        )
        if (
            is_question(clause)
            or has_embedded_question_word
            or _DISCUSSION_REQUEST_RE.search(clause) is not None
        ):
            return True
    return False


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
    explicit_task = any(
        word in message for word in ("网页", "网站", "问卷", "测试题", "答题")
    ) and (
        bool(re.search(r"https?://\S+", message))
        or any(word in message for word in ("做", "看", "打开", "填写"))
    )
    explicit_task = explicit_task or any(
        word in message for word in ("读书", "阅读", "读到第", "写一篇", "创作", "写点")
    )
    if explicit_task:
        return ContextMode.SLOW
    return (
        ContextMode.SLOW
        if slow_score(message, state, now, last_slow_at) >= threshold
        else ContextMode.FAST
    )
