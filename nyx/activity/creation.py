import random
from typing import Any, cast

from nyx.types import Activity, CurrentState, Memory

KNOWLEDGE_REF_CHARS = 80   # 创作知识参考单条截断字符数（decision，可推翻）
CREATION_STYLES = ("日记体", "随笔", "微型小说", "散文诗", "书信体", "观察笔记")


def pick_creation_style() -> str:
    """创作风格随机池：6 种里随机抽一种（W1）。"""
    return random.choice(CREATION_STYLES)


def build_creation_context(
    activity: Activity,
    style: str,
    knowledge: list[Memory],
    observation: dict[str, str],
) -> str:
    """创作参考上下文：风格 + 主题 + 知识库参考 + 当前屏幕灵感。"""
    goal = activity.progress.get("goal")
    topic = ""
    if isinstance(goal, dict):
        t = cast(dict[str, Any], goal).get("topic")
        if isinstance(t, str):
            topic = t
    if not topic:
        desc = activity.progress.get("description")
        if isinstance(desc, str):
            topic = desc
    parts = [f"风格：{style}"]
    if topic:
        parts.append(f"主题：{topic}")
    if knowledge:
        refs = "\n".join(
            f"- {m.summary}：{m.content[:KNOWLEDGE_REF_CHARS]}"
            for m in knowledge[:3]
        )
        parts.append(f"知识库参考（可引用，勿编造）：\n{refs}")
    window = observation.get("window_title", "").strip()
    screen = observation.get("screen_summary", "").strip()
    if window or screen:
        insp = "；".join(x for x in (window, screen) if x)
        parts.append(f"当前屏幕灵感：{insp}")
    return "\n\n".join(parts)


def build_creation_system(canon: str, state: CurrentState) -> str:
    """创作 system prompt：canon 人格全文 + 此刻心境 + 创作声音指令。"""
    desires = "、".join(d.description for d in state.active_desires) or "无"
    mood = (
        "[此刻心境]\n"
        f"情感：{state.emotion.value}"
        f"（valence={state.valence:.2f}，arousal={state.arousal:.2f}）\n"
        f"精力：{state.energy:.0f}/100\n"
        f"惦记：{desires}"
    )
    voice = (
        "[创作要求]\n"
        "以尼克斯的说话风格写：温柔克制安静真诚、带一点羞涩犹豫停顿、偶尔轻微自我修正，"
        "不要客服腔、不要堆砌华丽词藻，让文字有你的情绪底色。"
        "遵循给定风格，可引用知识库参考，但绝不编造不存在的知识；"
        "当前屏幕灵感只作启发，勿照搬。按 JSON 输出 {title, content}。"
    )
    return f"{canon}\n\n{mood}\n\n{voice}"
