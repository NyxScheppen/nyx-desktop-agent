import random
from typing import Any, cast

from nyx.inner_life.document import render_document
from nyx.policy import policy
from nyx.types import Activity, CurrentState, Memory

KNOWLEDGE_REF_CHARS = policy(
    "activity", "creation.KNOWLEDGE_REF_CHARS", int
)  # 创作知识参考单条截断字符数（decision，可推翻）
CREATION_STYLES = policy("activity", "creation.CREATION_STYLES", tuple[str, ...])


def pick_creation_style() -> str:
    """创作风格随机池：6 种里随机抽一种（W1）。"""
    return random.choice(CREATION_STYLES)


def creation_subject(activity: Activity) -> str:
    """创作活动的检索主题：goal.topic 优先，回退欲望描述。"""
    goal = activity.progress.get("goal")
    if isinstance(goal, dict):
        topic = cast(dict[str, Any], goal).get("topic")
        if isinstance(topic, str) and topic.strip():
            return topic.strip()
    description = activity.progress.get("description")
    if isinstance(description, str):
        return description.strip()
    return ""


def build_creation_context(
    activity: Activity,
    style: str,
    references: list[Memory],
    observation: dict[str, str],
) -> str:
    """创作参考上下文：风格 + 主题 + 知识库参考 + 当前屏幕灵感。"""
    topic = creation_subject(activity)
    parts = [
        policy(
            "activity", "creation.templates.build_creation_context_text_2", str
        ).format(p0=style)
    ]
    if topic:
        parts.append(
            policy(
                "activity", "creation.templates.build_creation_context_text_3", str
            ).format(p0=topic)
        )
    if references:
        refs = "\n".join(
            f"- {m.summary}：{m.content[:KNOWLEDGE_REF_CHARS]}"
            for m in references[
                : policy(
                    "activity",
                    "creation.parameters.build_creation_context_refs_chars_or_items",
                    int,
                )
            ]
        )
        parts.append(
            policy(
                "activity", "creation.templates.build_creation_context_text", str
            ).format(p0=refs)
        )
    window = observation.get("window_title", "").strip()
    screen = observation.get("screen_summary", "").strip()
    if window or screen:
        insp = "；".join(x for x in (window, screen) if x)
        parts.append(
            policy(
                "activity", "creation.templates.build_creation_context_text_4", str
            ).format(p0=insp)
        )
    return "\n\n".join(parts)


def build_creation_system(canon: str, state: CurrentState) -> str:
    """创作 system prompt：原型资料 + 当前内在文档 + 此刻心境 + 创作要求。"""
    desires = "、".join(d.description for d in state.active_desires) or policy(
        "activity", "creation.copy.build_creation_system_text", str
    )
    mood = policy(
        "activity", "creation.templates.build_creation_system_text", str
    ).format(
        p0=state.emotion.value,
        p1=state.valence,
        p2=state.arousal,
        p3=state.energy,
        p4=desires,
    )
    voice = (
        policy("activity", "creation.parameters.build_creation_system_voice_text", str)
    )
    return policy(
        "activity", "creation.templates.build_creation_system_text_2", str
    ).format(p0=canon, p1=render_document(state.inner_document), p2=mood, p3=voice)
