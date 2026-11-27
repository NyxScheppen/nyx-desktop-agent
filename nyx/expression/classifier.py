"""表达分类纯函数：快慢通道、问句和轻量用户意图。"""

import re
from collections.abc import Sequence

from nyx.enums import ContextMode, UserIntent
from nyx.policy import policy
from nyx.types import CurrentState

# 消息长度归一化：≥50 字符视为长消息（可推翻）
_LONG_MSG_LEN = policy("expression", "classifier._LONG_MSG_LEN", float)
# 距上次慢通道归一化：≥3600 秒（1 小时）视为满（可推翻）
_RECENCY_WINDOW = policy("expression", "classifier._RECENCY_WINDOW", float)
QUESTION_MARKS = policy("expression", "classifier.QUESTION_MARKS", tuple[str, ...])
_QUESTION_WORDS = policy("expression", "classifier._QUESTION_WORDS", tuple[str, ...])
_REQUEST_WORDS = policy("expression", "classifier._REQUEST_WORDS", tuple[str, ...])
_DISCUSSION_TERMS = policy(
    "expression", "classifier._DISCUSSION_TERMS", tuple[str, ...]
)
_DISCUSSION_REQUEST_RE = policy(
    "expression", "classifier._DISCUSSION_REQUEST_RE", re.Pattern[str]
)
_DISCUSSION_OPEN_RE = policy(
    "expression", "classifier._DISCUSSION_OPEN_RE", re.Pattern[str]
)
_DISCUSSION_NEGATION_RE = policy(
    "expression", "classifier._DISCUSSION_NEGATION_RE", re.Pattern[str]
)
_CASUAL_QUESTION_RE = policy(
    "expression", "classifier._CASUAL_QUESTION_RE", re.Pattern[str]
)
_EMOTIONAL_WORDS = policy("expression", "classifier._EMOTIONAL_WORDS", tuple[str, ...])
_GREETING_RE = policy("expression", "classifier._GREETING_RE", re.Pattern[str])
_EMOTION_WORDS = policy("expression", "classifier._EMOTION_WORDS", tuple[str, ...])


def derive_request_text(messages: Sequence[str]) -> tuple[int, str]:
    """Only an exact first control clause replaces the whole request."""
    controls = policy(
        "expression", "classifier.copy.derive_request_text_words", set[str]
    )
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
        if clause.endswith(
            policy("expression", "classifier.copy.is_question_words", tuple[str, ...])
        ):
            return True
        if clause.startswith(_QUESTION_WORDS):
            return True
        if clause.endswith(
            policy("expression", "classifier.copy.is_question_words_2", tuple[str, ...])
        ):
            return True
        if any(
            clause.startswith(prefix)
            for prefix in policy(
                "expression", "classifier.copy.is_question_words_3", tuple[str, ...]
            )
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
            policy("expression", "classifier.copy.should_load_discussion_text", str)
        ):
            return True
        if _CASUAL_QUESTION_RE.search(clause) and not any(
            term in clause
            for term in _DISCUSSION_TERMS
            if term
            != policy(
                "expression", "classifier.copy.should_load_discussion_text_2", str
            )
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
    """慢通道倾向得分 0-1，越高越该走慢通道。

    5 因子（权重和=1）：消息长度 0.25 + 含问句 0.25 + 情感词 0.20
    + 精力/情感 0.15 + 距上次慢通道 0.15。
    「精力/情感」= 精力足且情绪平静 → 倾向慢（有力气深聊）；精力低或激动 → 倾向快。
    """
    length = min(1.0, len(message) / _LONG_MSG_LEN)
    question = 1.0 if is_question(message) else 0.0
    emotion = 1.0 if any(w in message for w in _EMOTION_WORDS) else 0.0
    # 不夹：energy/arousal 已在上游 clamp 到 [0,100]/[0,1]
    vigor = policy("expression", "classifier.parameters.energy_vigor_weight", float) * (
        state.energy / policy("inner_life", "energy.maximum", float)
    ) + policy("expression", "classifier.parameters.arousal_vigor_weight", float) * (
        1.0 - state.arousal
    )
    # 上下限都夹：last_slow_at>now（时钟回拨）也不为负
    recency = max(0.0, min(1.0, (now - last_slow_at) / _RECENCY_WINDOW))
    return (
        policy("expression", "classifier.parameters.length_weight", float) * length
        + policy("expression", "classifier.parameters.question_weight", float)
        * question
        + policy("expression", "classifier.parameters.emotion_weight", float) * emotion
        + policy("expression", "classifier.parameters.vigor_weight", float) * vigor
        + policy("expression", "classifier.parameters.recency_weight", float) * recency
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
        word in message
        for word in policy(
            "expression", "classifier.copy.classify_channel_words", tuple[str, ...]
        )
    ) and (
        bool(re.search(r"https?://\S+", message))
        or any(
            word in message
            for word in policy(
                "expression",
                "classifier.copy.classify_channel_words_3",
                tuple[str, ...],
            )
        )
    )
    explicit_task = explicit_task or any(
        word in message
        for word in policy(
            "expression", "classifier.copy.classify_channel_words_2", tuple[str, ...]
        )
    )
    if explicit_task or policy(
        "initiative", "patterns.tool_intent", re.Pattern[str]
    ).search(message):
        return ContextMode.SLOW
    return (
        ContextMode.SLOW
        if slow_score(message, state, now, last_slow_at) >= threshold
        else ContextMode.FAST
    )
