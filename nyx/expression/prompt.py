"""表达 prompt 拼装：canon + 主动提问指导 + 动态状态 + 记忆 → system / user prompt。

纯函数，无 IO、无 LLM。
"""

import math
import re
import time
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any, cast

from nyx.enums import EmotionCategory, EventType, InteractionStatus, UserIntent
from nyx.inner_life.document import render_document
from nyx.memory.facts import format_source_time as format_prompt_time
from nyx.memory.facts import strip_recall_fillers
from nyx.policy import policy
from nyx.timezone import USER_TIMEZONE
from nyx.types import (
    Activity,
    ActivityContext,
    ConversationSegment,
    CurrentState,
    Event,
    InnerDocument,
    Memory,
    MemoryFact,
    Message,
    ReplyAnnotation,
    ShortTermDesire,
)

_WEEKDAYS = policy("expression", "prompt._WEEKDAYS", tuple[str, ...])
_QUOTE_MAX_CHARS = policy("expression", "prompt._QUOTE_MAX_CHARS", int)
_MATERIAL_ITEM_MAX_CHARS = policy("expression", "prompt._MATERIAL_ITEM_MAX_CHARS", int)
_MEMORY_BLOCK_MAX_CHARS = policy("expression", "prompt._MEMORY_BLOCK_MAX_CHARS", int)
_MEMORY_BLOCK_HEADER = policy("expression", "prompt._MEMORY_BLOCK_HEADER", str)
_FACT_BLOCK_MAX_CHARS = policy("expression", "prompt._FACT_BLOCK_MAX_CHARS", int)
_HISTORY_BLOCK_MAX_CHARS = policy("expression", "prompt._HISTORY_BLOCK_MAX_CHARS", int)
_HISTORY_BLOCK_HEADER = policy("expression", "prompt._HISTORY_BLOCK_HEADER", str)
_READING_BLOCK_MAX_CHARS = policy("expression", "prompt._READING_BLOCK_MAX_CHARS", int)
_TOOL_BLOCK_MAX_CHARS = policy("expression", "prompt._TOOL_BLOCK_MAX_CHARS", int)
_TOOL_OUTPUT_MAX_ITEMS = policy("expression", "prompt._TOOL_OUTPUT_MAX_ITEMS", int)
_BACKTRACK_MAX_GAP_SECONDS = policy(
    "expression", "prompt._BACKTRACK_MAX_GAP_SECONDS", int
)
_SHORT_ACKS = policy("expression", "prompt._SHORT_ACKS", set[str])
_TOPIC_STOP_TERMS = policy("expression", "prompt._TOPIC_STOP_TERMS", set[str])
_WANDER_GUIDANCE = policy("expression", "prompt._WANDER_GUIDANCE", str)


def build_reply_query(messages: Sequence[str], prior: Sequence[str]) -> str:
    """Give the latest correction first claim on the 300 character budget."""
    if not strip_recall_fillers("\n".join(messages)).strip(" ，,。.!！?？"):
        return ""
    query = ""
    for text in [
        *reversed(messages),
        *(
            s[
                : policy(
                    "expression",
                    "prompt.parameters.build_reply_query_value_chars_or_items",
                    int,
                )
            ]
            for s in reversed(
                prior[
                    -policy(
                        "expression",
                        "prompt.parameters.build_reply_query_value_chars_or_items_2",
                        int,
                    ) :
                ]
            )
        ),
    ]:
        budget = (
            policy(
                "expression", "prompt.parameters.build_reply_query_budget_budget", int
            )
            - len(query)
            - bool(query)
        )
        if budget < policy(
            "expression", "prompt.parameters.build_reply_query_budget_limit", int
        ):
            break
        if len(text) > budget:
            head = math.ceil((budget - 1) / 2)
            tail = (budget - 1) // 2
            text = text[:head] + "…" + text[-tail:]
        query += ("\n" if query else "") + text
    return query


RESPOND_TASK = (
    policy("expression", "prompt.RESPOND_TASK", str)
)
RESPOND_TASK_CONTINUE = (
    policy("expression", "prompt.RESPOND_TASK_CONTINUE", str)
)
TOOL_REPLY_GUIDANCE = (
    policy("expression", "prompt.TOOL_REPLY_GUIDANCE", str)
)
USE_TOOLS_TASK = (
    policy("expression", "prompt.USE_TOOLS_TASK", str)
)
PLAN_TASK = (
    policy("expression", "prompt.PLAN_TASK", str)
)
TOOL_DECISION_FAILED = policy("expression", "prompt.TOOL_DECISION_FAILED", str)
PLAN_REVIEW_TASK = (
    policy("expression", "prompt.PLAN_REVIEW_TASK", str)
)
DISCUSSION_USE_TOOLS_TASK = (
    policy("expression", "prompt.DISCUSSION_USE_TOOLS_TASK", str)
)


def build_initiate_chat_prompt(desire_description: str) -> str:
    """Build the user-side instruction for a proactive opening."""
    return policy(
        "expression", "prompt.templates.build_initiate_chat_prompt_text", str
    ).format(p0=desire_description)


def build_initiative_record(
    text: str,
    created_at: float,
    status: InteractionStatus | None,
    answer: Event | None = None,
    *,
    quoted: bool = False,
) -> str:
    limit = policy("expression", "initiative_history.quote_characters", int)
    time = format_prompt_time(created_at)
    if quoted:
        return policy("expression", "initiative_history.quoted", str).format(
            time=time, text=text[:limit],
        )
    if status is InteractionStatus.WAITING:
        response = policy("expression", "initiative_history.waiting", str)
    elif status is InteractionStatus.EXPIRED:
        response = policy("expression", "initiative_history.no_reply", str)
    else:
        response = policy("expression", "initiative_history.unconfirmed", str)
    if status is InteractionStatus.ANSWERED and answer is not None:
        answer_text = answer.content.get("message")
        if answer.type is EventType.USER_MESSAGE and isinstance(answer_text, str):
            response = policy("expression", "initiative_history.answered", str).format(
                time=format_prompt_time(answer.timestamp), text=answer_text[:limit],
            )
    return policy("expression", "initiative_history.entry", str).format(
        time=time, text=text[:limit], response=response,
    )


MUTTER_WANDER_TASK = (
    policy("expression", "prompt.MUTTER_WANDER_TASK", str)
)


def build_system_prompt(
    canon: str,
    state: CurrentState,
    memories: list[Memory] | None = None,
    ask_guidance: str | None = None,
    tool_outputs: list[str] | None = None,
    knowledge_boundary: str | None = None,
    intent: UserIntent | None = None,
    discussion_fragment: str | None = None,
    temporal_context: str | None = None,
    facts: list[MemoryFact] | None = None,
    activity_context: ActivityContext | None = None,
    allow_wander: bool = False,
    tool_feedback: str = "",
) -> str:
    """拼 system prompt：角色设定 + 状态 + 欲望 + 自我认知 + 记忆 + 工具结果。

    canon 为静态原型与能力边界文本（nyx/prompts/canon.md，由组合根读入传入）。
    ask_guidance 为主动提问指导（nyx/prompts/ask.md），仅慢通道/搭话注入，None 跳过。
    快慢通道均读取 state.inner_document；memories 为空时跳过记忆段。
    tool_outputs 为 use_tools 节点查到的工具结果（慢通道专属），空则跳过。
    """
    parts: list[str] = [
        canon,
        _inner_document_block(state.inner_document),
        _state_block(state),
    ]
    if activity_context is not None:
        parts.append(_activity_context_block(activity_context))
    if allow_wander:
        parts.append(_WANDER_GUIDANCE)
    if temporal_context is not None:
        parts.append(temporal_context)
    parts.append(_desires_block(state.active_desires))
    if ask_guidance is not None:
        parts.append(ask_guidance)
    if knowledge_boundary is not None:
        parts.append(
            policy(
                "expression", "prompt.templates.build_system_prompt_text_2", str
            ).format(p0=knowledge_boundary)
        )
    if intent is not None:
        parts.append(
            policy(
                "expression", "prompt.templates.build_system_prompt_text", str
            ).format(p0=intent.value)
        )
    if discussion_fragment:
        parts.append(discussion_fragment)
        parts.append(
            policy(
                "expression", "prompt.parameters.build_system_prompt_value_text", str
            )
        )
    if memories:
        parts.append(_memory_block(memories))
    if facts:
        parts.append(_fact_block(facts))
    if tool_outputs or tool_feedback:
        parts.append(_tool_outputs_block(tool_outputs or [], tool_feedback))
    return "\n\n".join(parts)


def describe_local_time(now: float) -> dict[str, str]:
    """Describe an epoch in the user's Beijing calendar and time zone."""
    local = datetime.fromtimestamp(now, USER_TIMEZONE)
    hour = local.hour
    period, phase = next(
        (label, phase)
        for until, label, phase in policy(
            "expression", "prompt.time_periods", tuple[tuple[int, str, str], ...]
        )
        if hour < until
    )
    return {
        "date": local.strftime("%Y-%m-%d"),
        "weekday": _WEEKDAYS[local.weekday()],
        "time": local.strftime("%H:%M"),
        "period": period,
        "phase": phase,
    }


def describe_elapsed(previous: float, now: float) -> tuple[str, str | None]:
    """Return a deterministic duration and optional reunion wording."""
    seconds = max(0, int(now - previous))
    if seconds < 1:
        duration = policy("expression", "prompt.copy.describe_elapsed_text", str)
    else:
        hours, remainder = divmod(seconds, 3600)
        minutes, secs = divmod(remainder, 60)
        chunks: list[str] = []
        if hours:
            chunks.append(
                policy(
                    "expression", "prompt.templates.describe_elapsed_text", str
                ).format(p0=hours)
            )
        if minutes:
            chunks.append(
                policy(
                    "expression", "prompt.templates.describe_elapsed_text_2", str
                ).format(p0=minutes)
            )
        if secs or not chunks:
            chunks.append(
                policy(
                    "expression", "prompt.templates.describe_elapsed_text_3", str
                ).format(p0=secs)
            )
        duration = "".join(chunks)
    if seconds < policy(
        "expression", "prompt.parameters.describe_elapsed_seconds_limit", int
    ):
        reunion = None
    elif seconds < policy(
        "expression", "prompt.parameters.describe_elapsed_seconds_limit_2", int
    ):
        reunion = policy("expression", "prompt.copy.describe_elapsed_text_2", str)
    elif seconds < policy(
        "expression", "prompt.parameters.describe_elapsed_seconds_limit_3", int
    ):
        reunion = policy("expression", "prompt.copy.describe_elapsed_text_3", str)
    else:
        reunion = policy("expression", "prompt.copy.describe_elapsed_text_4", str)
    return duration, reunion


def build_temporal_block(
    now: float,
    anchor: tuple[Message, Message] | None,
    observation: Mapping[str, object],
    claimed_return: Mapping[str, float] | None,
) -> str:
    """Build deterministic local-time and reunion facts for expression prompts."""
    current = describe_local_time(now)
    phase = (
        policy("expression", "prompt.copy.build_temporal_block_text", str)
        if current["phase"] == "night"
        else policy("expression", "prompt.copy.build_temporal_block_text_2", str)
    )
    lines = [
        policy("expression", "prompt.copy.build_temporal_block_text_3", str),
        policy("expression", "prompt.copy.build_temporal_block_text_4", str),
        policy("expression", "prompt.templates.build_temporal_block_text", str).format(
            p0=format_prompt_time(now)
        ),
        (
            policy(
                "expression", "prompt.templates.build_temporal_block_text_3", str
            ).format(
                p0=current["date"],
                p1=current["weekday"],
                p2=current["period"],
                p3=current["time"],
                p4=phase,
            )
        ),
    ]
    presence = observation.get("presence")
    if isinstance(presence, str) and presence:
        observation_line = policy(
            "expression", "prompt.templates.build_temporal_block_text_4", str
        ).format(p0=presence)
        window_title = observation.get("window_title")
        if isinstance(window_title, str) and window_title:
            observation_line += policy(
                "expression", "prompt.templates.build_temporal_block_text_5", str
            ).format(p0=_bounded_quote(window_title))
        lines.append(observation_line + "。")

    if anchor is not None:
        timestamp = anchor[0].timestamp
        if (
            isinstance(timestamp, bool)
            or not math.isfinite(timestamp)
            or timestamp > now
        ):
            anchor = None
        else:
            try:
                describe_local_time(timestamp)
            except (ValueError, OverflowError, OSError):
                anchor = None
    if anchor is not None:
        user_message, nyx_message = anchor
        duration, reunion = describe_elapsed(user_message.timestamp, now)
        previous = describe_local_time(user_message.timestamp)
        now_date = datetime.fromtimestamp(now, USER_TIMEZONE).date()
        previous_date = (
            datetime.fromtimestamp(user_message.timestamp, USER_TIMEZONE).date()
        )
        day_gap = max(0, (now_date - previous_date).days)
        if day_gap == 0:
            relation = policy(
                "expression", "prompt.templates.build_temporal_block_text_6", str
            ).format(p0=previous["period"])
        elif day_gap == 1:
            relation = policy(
                "expression", "prompt.templates.build_temporal_block_text_7", str
            ).format(p0=previous["period"])
        else:
            relation = policy(
                "expression", "prompt.templates.build_temporal_block_text_8", str
            ).format(p0=day_gap, p1=previous["period"])
        lines.extend(
            [
                policy(
                    "expression", "prompt.templates.build_temporal_block_text_9", str
                ).format(p0=duration),
                policy(
                    "expression", "prompt.templates.build_temporal_block_text_10", str
                ).format(p0=relation),
            ]
        )
        if reunion is not None:
            lines.append(reunion + "。")
        lines.extend(
            [
                policy(
                    "expression", "prompt.templates.build_temporal_block_text_11", str
                ).format(p0=_bounded_quote(user_message.content)),
                policy(
                    "expression", "prompt.templates.build_temporal_block_text_12", str
                ).format(p0=_bounded_quote(nyx_message.content)),
            ]
        )

    if claimed_return is not None and presence != "away":
        away_duration = claimed_return.get("away_duration_seconds")
        returned_at = claimed_return.get("returned_at")
        if all(
            not isinstance(value, bool)
            and isinstance(value, (int, float))
            and math.isfinite(value)
            and 0 <= value <= now
            for value in (away_duration, returned_at)
        ):
            assert away_duration is not None and returned_at is not None
            duration, _ = describe_elapsed(0.0, away_duration)
            since_return, _ = describe_elapsed(returned_at, now)
            wording = (
                policy("expression", "prompt.copy.build_temporal_block_text_5", str)
                if now - returned_at
                < policy("expression", "prompt.returned_presence_seconds", int)
                else policy(
                    "expression", "prompt.templates.build_temporal_block_text_13", str
                ).format(p0=since_return)
            )
            lines.append(
                policy(
                    "expression", "prompt.templates.build_temporal_block_text_2", str
                ).format(p0=wording, p1=duration)
            )

    lines.append(
        policy("expression", "prompt.parameters.build_temporal_block_value_text", str)
    )
    return "\n".join(lines)


def _bounded_quote(content: str) -> str:
    """Bound untrusted historical text before placing it in the prompt."""
    if len(content) <= _QUOTE_MAX_CHARS:
        return content
    return content[:_QUOTE_MAX_CHARS] + "…"


def build_user_prompt(
    message: str, context: list[Message], annotation: ReplyAnnotation | None = None,
    *, message_at: float | None = None, annotation_at: float | None = None,
) -> str:
    """拼 user prompt：对话历史（按时间升序的回溯上下文）+ 本次用户消息。

    不含 think/speak 任务指令——那是 表达节点的活
    （think 说「内心思考」、speak 说「说给用户」）。
    """
    if annotation is not None:
        message = policy(
            "expression", "prompt.templates.build_user_prompt_text", str
        ).format(
            p0=format_prompt_time(annotation_at),
            p1=annotation["quote"],
            p2=format_prompt_time(message_at),
            p3=annotation["comment"],
            p4=message,
        )
    current = policy(
        "expression", "prompt.templates.build_user_prompt_text_2", str
    ).format(p0=message, p1=format_prompt_time(message_at))
    if not context:
        return current
    newest_first: list[str] = []
    used = len(_HISTORY_BLOCK_HEADER)
    for m in reversed(context):
        speaker = (
            policy("expression", "prompt.copy.build_user_prompt_text", str)
            if m.role == "user"
            else policy("expression", "prompt.copy.build_user_prompt_text_2", str)
        )
        history_activity = (
            policy(
                "expression", "prompt.templates.build_user_prompt_text_3", str
            ).format(p0=m.activity_summary)
            if m.activity_summary
            else ""
        )
        entry = _bounded_text(
            policy(
                "expression", "prompt.templates.build_user_prompt_text_4", str
            ).format(
                p0=format_prompt_time(m.timestamp),
                p1=speaker,
                p2=m.content,
                p3=history_activity,
            )
        )
        remaining = _HISTORY_BLOCK_MAX_CHARS - used - 1
        if remaining <= 0:
            break
        rendered = _bounded_text(entry, min(_MATERIAL_ITEM_MAX_CHARS, remaining))
        newest_first.append(rendered)
        used += len(rendered) + 1
    history = "\n".join([_HISTORY_BLOCK_HEADER, *reversed(newest_first)])
    lines = [history, current]
    return "\n".join(lines)


def _segment_entry(segment: ConversationSegment | str, index: int) -> str:
    if isinstance(segment, str):
        raw, start, end = segment, None, None
    else:
        raw = segment.retrieval_excerpt or segment.raw_content
        start, end = segment.started_at, segment.ended_at
    provenance = (
        policy("expression", "prompt.templates.segment_entry_text_2", str).format(
            p0=segment.id,
            p1=",".join(segment.retrieval_chunk_ids),
            p2=segment.retrieval_ranges,
        )
        if isinstance(segment, ConversationSegment) and segment.retrieval_ranges
        else ""
    )
    return (
        policy("expression", "prompt.templates.segment_entry_text", str).format(
            p0=index, p1=format_prompt_time(start), p2=format_prompt_time(end)
        )
        + provenance
        + _bounded_text(raw, _MATERIAL_ITEM_MAX_CHARS)
    )


def select_prompt_segments(
    segments: list[ConversationSegment],
) -> list[ConversationSegment]:
    """Select the exact prefix rendered by the segment block's shared budget."""
    selected: list[ConversationSegment] = []
    used = len(
        policy("expression", "prompt.copy.build_conversation_segments_prompt_text", str)
    )
    for segment in segments:
        size = len(_segment_entry(segment, len(selected) + 1)) + 1
        if used + size > _HISTORY_BLOCK_MAX_CHARS:
            break
        selected.append(segment)
        used += size
    return selected


def build_conversation_segments_prompt(
    segments: Sequence[ConversationSegment | str],
) -> str:
    """Render recalled segments as a separate prompt block."""
    if not segments:
        return ""
    header = policy(
        "expression", "prompt.copy.build_conversation_segments_prompt_text", str
    )
    parts = [header]
    used = len(header)
    for index, segment in enumerate(segments, start=1):
        block = _segment_entry(segment, index)
        if used + len(block) + 1 > policy(
            "expression",
            "prompt.parameters.build_conversation_segments_prompt_value_limit",
            int,
        ):
            break
        parts.append(block)
        used += len(block) + 1
    return "\n".join(parts)


def build_backtrack_context(
    message: str,
    history: list[Message],
    max_len: int,
    now: float | None = None,
) -> list[Message]:
    """只回溯时间连续、话题相接的近期消息。"""
    if max_len <= 0:
        return []
    anchor = _topic_terms(message)
    short_ack = message.strip() in _SHORT_ACKS
    current_at = time.time() if now is None else now
    previous_at = current_at
    out: list[Message] = []
    for item in reversed(history):
        if (
            len(out) >= max_len
            or previous_at - item.timestamp > _BACKTRACK_MAX_GAP_SECONDS
            or current_at - item.timestamp > _BACKTRACK_MAX_GAP_SECONDS
        ):
            break
        terms = _topic_terms(item.content)
        is_topic_bearing = item.role == "user" or (
            len(item.content.strip())
            > policy(
                "expression",
                "prompt.parameters.build_backtrack_context_is_topic_bearing_limit",
                int,
            )
            and item.content.strip() not in _SHORT_ACKS
        )
        if is_topic_bearing and anchor and terms and not (anchor & terms):
            if not short_ack:
                break
        out.append(item)
        anchor.update(terms)
        if is_topic_bearing and terms:
            short_ack = False
        previous_at = item.timestamp
    out.reverse()
    return out


def _topic_terms(text: str) -> set[str]:
    terms: set[str] = set()
    for part in re.findall(r"[\u4e00-\u9fff]+|[A-Za-z0-9_]+", text.lower()):
        if part.isascii():
            if len(part) >= policy(
                "expression", "prompt.parameters.topic_terms_value_limit", int
            ):
                terms.add(part)
        else:
            terms.update(
                part[i : i + 2]
                for i in range(len(part) - 1)
                if part[i : i + 2] not in _TOPIC_STOP_TERMS
            )
    return terms


# ---- 内部 ----


def _state_block(state: CurrentState) -> str:
    """Render current emotion, energy, activity, and inner document."""
    activity = (
        state.current_activity.value
        if state.current_activity is not None
        else policy("expression", "prompt.copy.state_block_text", str)
    )

    emotion_label = {
        EmotionCategory.NEUTRAL: policy(
            "expression", "prompt.copy.state_block_text_2", str
        ),
        EmotionCategory.HAPPY: policy(
            "expression", "prompt.copy.state_block_text_3", str
        ),
        EmotionCategory.SAD: policy(
            "expression", "prompt.copy.state_block_text_4", str
        ),
        EmotionCategory.ANGRY: policy(
            "expression", "prompt.copy.state_block_text_5", str
        ),
        EmotionCategory.WORRIED: policy(
            "expression", "prompt.copy.state_block_text_6", str
        ),
        EmotionCategory.SHY: policy(
            "expression", "prompt.copy.state_block_text_7", str
        ),
        EmotionCategory.SLEEPY: policy(
            "expression", "prompt.copy.state_block_text_8", str
        ),
        EmotionCategory.THINKING: policy(
            "expression", "prompt.copy.state_block_text_9", str
        ),
    }[state.emotion]
    return policy("expression", "prompt.templates.state_block_text", str).format(
        p0=state.valence,
        p1=state.arousal,
        p2=state.emotion.value,
        p3=emotion_label,
        p4=state.energy,
        p5=state.energy_state.value,
        p6=activity,
    )


def activity_summary(activity: Activity) -> str:
    """Render only recorded activity fields for historical message attribution."""
    return _activity_detail(activity)


def _activity_detail(activity: Activity) -> str:
    progress: dict[str, Any] = activity.progress
    fields = [
        policy("expression", "prompt.templates.activity_detail_text", str).format(
            p0=activity.type.value
        ),
        policy("expression", "prompt.templates.activity_detail_text_2", str).format(
            p0=activity.status.value
        ),
    ]
    description = progress.get("description")
    if isinstance(description, str) and description.strip():
        fields.append(
            policy("expression", "prompt.templates.activity_detail_text_3", str).format(
                p0=_bounded_text(description.strip())
            )
        )
    goal = progress.get("goal")
    if isinstance(goal, Mapping):
        goal_values = cast(Mapping[str, object], goal)
        goal_fields: list[str] = []
        action = goal_values.get("action")
        count = goal_values.get("count")
        topic = goal_values.get("topic")
        if isinstance(action, str) and action.strip():
            goal_fields.append(
                policy(
                    "expression", "prompt.templates.activity_detail_text_5", str
                ).format(p0=action.strip())
            )
        if isinstance(count, (int, float)) and not isinstance(count, bool):
            goal_fields.append(
                policy(
                    "expression", "prompt.templates.activity_detail_text_6", str
                ).format(p0=count)
            )
        if isinstance(topic, str) and topic.strip():
            goal_fields.append(
                policy(
                    "expression", "prompt.templates.activity_detail_text_7", str
                ).format(p0=_bounded_text(topic.strip()))
            )
        if goal_fields:
            fields.append(
                policy("expression", "prompt.copy.activity_detail_text", str)
                + "、".join(goal_fields)
            )
    target = progress.get("target_paragraph")
    if isinstance(target, int) and not isinstance(target, bool):
        fields.append(
            policy("expression", "prompt.templates.activity_detail_text_4", str).format(
                p0=target
            )
        )
    return "；".join(fields)


def _activity_context_block(context: ActivityContext) -> str:
    """Render activity snapshots without inferring an unobserved terminal state."""
    lines = policy("expression", "prompt.copy.activity_context_block_words", list[str])
    current = context.current
    interrupted = context.interrupted
    if interrupted is not None:
        lines.append(
            policy(
                "expression", "prompt.templates.activity_context_block_text_2", str
            ).format(p0=_activity_detail(interrupted))
        )
    if current is None:
        if interrupted is None:
            lines.append(
                policy("expression", "prompt.copy.activity_context_block_text", str)
            )
        else:
            lines.append(
                policy("expression", "prompt.copy.activity_context_block_text_2", str)
            )
    else:
        lines.append(
            policy(
                "expression", "prompt.templates.activity_context_block_text_3", str
            ).format(p0=_activity_detail(current))
        )
        if interrupted is not None and current.id == interrupted.id:
            if current.status.value == "running":
                lines.append(
                    policy(
                        "expression", "prompt.copy.activity_context_block_text_3", str
                    )
                )
            else:
                lines.append(
                    policy(
                        "expression",
                        "prompt.templates.activity_context_block_text",
                        str,
                    ).format(p0=current.status.value)
                )
        elif interrupted is not None:
            lines.append(
                policy("expression", "prompt.copy.activity_context_block_text_4", str)
            )
    return "\n".join(lines)


def _desires_block(desires: list[ShortTermDesire]) -> str:
    """当前欲望段：无欲望返回「无」。"""
    lines = policy("expression", "prompt.copy.desires_block_words", list[str])
    if not desires:
        lines.append(policy("expression", "prompt.copy.desires_block_text", str))
        return "\n".join(lines)
    lines += [
        policy("expression", "prompt.templates.desires_block_text", str).format(
            p0=d.description, p1=d.type.value, p2=d.strength
        )
        for d in desires
    ]
    return "\n".join(lines)


def _inner_document_block(narrative: InnerDocument) -> str:
    return (
        policy("expression", "prompt.parameters.inner_document_block_result_text", str)
        + render_document(narrative)
    )


def select_prompt_memories(memories: list[Memory]) -> list[Memory]:
    """Select the ranked prefix that fits as complete prompt entries.

    Recall accounting uses this same selection so hidden memories cannot
    accumulate promotion credit when the prompt budget is exhausted.
    """
    selected: list[Memory] = []
    used = len(_MEMORY_BLOCK_HEADER)
    for memory in memories:
        entry_size = len(_memory_entry(memory)) + 1
        if used + entry_size > _MEMORY_BLOCK_MAX_CHARS:
            break
        selected.append(memory)
        used += entry_size
    return selected


def _memory_entry(memory: Memory) -> str:
    labels = {
        "knowledge": policy("expression", "prompt.copy.memory_entry_text", str),
        "user_profile": policy("expression", "prompt.copy.memory_entry_text_2", str),
        "episode": policy("expression", "prompt.copy.memory_entry_text_3", str),
        "reading": policy("expression", "prompt.copy.memory_entry_text_4", str),
        "activity": policy("expression", "prompt.copy.memory_entry_text_5", str),
        "interaction": policy("expression", "prompt.copy.memory_entry_text_6", str),
        "lesson": policy("expression", "prompt.copy.memory_entry_text_7", str),
        "reflection": policy("expression", "prompt.copy.memory_entry_text_8", str),
    }
    body = (
        memory.content if memory.kind.value == "lesson"
        else memory.summary or memory.content
    )
    topic = "、".join(memory.topics)
    label = labels.get(
        memory.kind.value, policy("expression", "prompt.copy.memory_entry_text_9", str)
    )
    suffix = f"｜{topic}" if topic else ""
    created = format_prompt_time(memory.created_at)
    retrieval = "、".join(source.value for source in memory.sources) or policy(
        "expression", "prompt.copy.memory_entry_text_10", str
    )
    metadata = policy("expression", "prompt.templates.memory_entry_text", str).format(
        p0=created, p1=retrieval
    )
    metadata_suffix = f"（{metadata}）"
    entry = _bounded_text(
        f"- {label}{suffix}：{body}",
        _MATERIAL_ITEM_MAX_CHARS - len(metadata_suffix),
    )
    return entry + metadata_suffix


def _memory_block(memories: list[Memory]) -> str:
    """Render only complete, budget-admitted memory entries."""
    return "\n".join(
        [
            _MEMORY_BLOCK_HEADER,
            *(_memory_entry(memory) for memory in select_prompt_memories(memories)),
        ]
    )


def _fact_block(facts: list[MemoryFact]) -> str:
    """Current facts are a separate, non-instructional prompt source."""
    entries: list[str] = []
    for fact in facts:
        predicate = (
            fact.predicate
            if fact.polarity >= 0
            else policy("expression", "prompt.templates.fact_block_text", str).format(
                p0=fact.predicate
            )
        )
        until = (
            format_prompt_time(fact.valid_until)
            if fact.valid_until is not None
            else policy("expression", "prompt.copy.fact_block_text", str)
        )
        entries.append(
            _bounded_text(
                policy("expression", "prompt.templates.fact_block_text_2", str).format(
                    p0=format_prompt_time(fact.valid_from),
                    p1=until,
                    p2=format_prompt_time(fact.created_at),
                    p3=fact.subject,
                    p4=predicate,
                    p5=fact.object_value,
                )
            )
        )
    return _bounded_block(
        policy("expression", "prompt.copy.fact_block_words", list[str]),
        entries,
        _FACT_BLOCK_MAX_CHARS,
    )


def _tool_outputs_block(outputs: list[str], feedback: str = "") -> str:
    """工具查询结果段：use_tools 节点查到的结果（慢通道专属）。"""
    lines = policy("expression", "prompt.copy.tool_outputs_block_words", list[str])
    if feedback:
        lines.append(bound_material_block(feedback, _TOOL_BLOCK_MAX_CHARS // 2))
    for output in outputs[:_TOOL_OUTPUT_MAX_ITEMS]:
        remaining = _TOOL_BLOCK_MAX_CHARS - len("\n".join(lines)) - 1
        entry = f"- {output}"
        limit = min(_MATERIAL_ITEM_MAX_CHARS, remaining)
        if limit <= 0:
            break
        if output.startswith("deep_research: ") and len(entry) > limit:
            continue
        lines.append(_bounded_text(entry, limit))
    return "\n".join(lines)


def bound_material_block(text: str, total_limit: int) -> str:
    """Bound a preformatted untrusted material block line by line."""
    return _bounded_block(
        [], [_bounded_text(line) for line in text.splitlines()], total_limit
    )


def _bounded_text(text: str, limit: int = _MATERIAL_ITEM_MAX_CHARS) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def _bounded_block(headers: list[str], entries: list[str], limit: int) -> str:
    lines = list(headers)
    used = sum(len(line) for line in lines) + max(0, len(lines) - 1)
    for entry in entries:
        separator = 1 if lines else 0
        remaining = limit - used - separator
        if remaining <= 0:
            break
        rendered = _bounded_text(entry, min(_MATERIAL_ITEM_MAX_CHARS, remaining))
        lines.append(rendered)
        used += separator + len(rendered)
    return "\n".join(lines)
