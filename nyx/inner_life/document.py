"""The current inner document and exact, evidence-backed topic revisions."""

import re
from collections.abc import Mapping
from dataclasses import asdict
from typing import Any, cast

from nyx.types import InnerDocument, InnerTopic

SECTIONS = {
    "identity": "自我理解",
    "worldview": "世界观",
    "relationships": "关系",
    "motivation": "动机",
    "personality": "人格",
    "aesthetic": "审美",
    "narrative": "个人叙事",
    "questions": "尚未想明白的事",
}
MAX_DOCUMENT_CHARS = 24000
MAX_TOPIC_CHARS = 6000
MAX_TOPICS = 64


def validate_topics(topics: list[InnerTopic]) -> None:
    """Reject ambiguous anchors and invalid/oversized current documents."""
    if not topics or len(topics) > MAX_TOPICS:
        raise ValueError("内在文档须包含 1–64 个主题")
    seen: set[str] = set()
    for topic in topics:
        if topic.section not in SECTIONS:
            raise ValueError("未知内在文档部分")
        if not re.fullmatch(r"[a-z][a-z0-9-]{0,79}", topic.id) or topic.id in seen:
            raise ValueError("主题 ID 非法或重复")
        seen.add(topic.id)
        if not topic.title.strip() or len(topic.title) > 100:
            raise ValueError("主题标题须为 1–100 字")
        if not topic.content.strip() or len(topic.content) > MAX_TOPIC_CHARS:
            raise ValueError("主题正文须为 1–6000 字")
    if (
        sum(len(t.content) + len(t.title) + len(t.id) for t in topics)
        > MAX_DOCUMENT_CHARS
    ):
        raise ValueError("内在文档超过 24000 字")


def render_document(document: InnerDocument, section: str | None = None) -> str:
    """Render stable anchors with prose; the document cannot grant capabilities."""
    parts: list[str] = []
    for key, label in SECTIONS.items():
        selected = [t for t in document.topics if t.section == key]
        if (section is not None and section != key) or not selected:
            continue
        parts.append(f"## {label} [{key}]")
        parts.extend(f"### {t.title} [{t.id}]\n{t.content}" for t in selected)
    return "\n\n".join(parts)


def topics_from_data(data: Any) -> list[InnerTopic]:
    if not isinstance(data, list):
        raise ValueError("内在文档主题须为数组")
    topics: list[InnerTopic] = []
    for value in cast(list[Any], data):
        if not isinstance(value, dict):
            raise ValueError("内在文档主题须为对象")
        item = cast(dict[str, Any], value)
        if set(item) != {"section", "id", "title", "content"} or any(
            not isinstance(v, str) for v in item.values()
        ):
            raise ValueError("内在文档主题字段非法")
        topics.append(InnerTopic(**item))
    validate_topics(topics)
    return topics


def revise_document(
    current: InnerDocument,
    patches: Any,
    evidence: Mapping[str, str],
    now: float,
) -> InnerDocument:
    """Apply all patches in memory or reject the whole revision before writing."""
    if not isinstance(patches, list) or len(cast(list[Any], patches)) > 16:
        raise ValueError("主题修订须为不超过 16 项的数组")
    topics = [InnerTopic(**asdict(t)) for t in current.topics]
    seen: set[str] = set()
    for raw in cast(list[Any], patches):
        if not isinstance(raw, dict):
            raise ValueError("修订须为对象")
        patch = cast(dict[str, Any], raw)
        section, identity = patch.get("section"), patch.get("id")
        if (
            not isinstance(identity, str)
            or identity in seen
            or not isinstance(section, str)
            or section not in SECTIONS
        ):
            raise ValueError("修订目标非法或重复")
        seen.add(identity)
        reason = patch.get("reason")
        refs = patch.get("evidence")
        if (
            not isinstance(reason, str)
            or not reason.strip()
            or not isinstance(refs, list)
            or not refs
        ):
            raise ValueError("修改须说明理由并提供来源引用")
        for reference in cast(list[Any], refs):
            if not isinstance(reference, dict):
                raise ValueError("引用格式非法")
            ref = cast(dict[str, Any], reference)
            source, quote = ref.get("id"), ref.get("quote")
            if (
                not isinstance(source, str)
                or source not in evidence
                or not isinstance(quote, str)
                or not quote.strip()
                or quote not in evidence[source]
            ):
                raise ValueError("修订引用未命中输入原文")
        existing = next((t for t in topics if t.id == identity), None)
        if existing is not None and existing.section != section:
            raise ValueError("主题与所属部分不匹配")
        operation = patch.get("op")
        if operation == "remove":
            if existing is None:
                raise ValueError("无法删除不存在的主题")
            topics.remove(existing)
        elif operation == "set":
            title, content = patch.get("title"), patch.get("content")
            if not isinstance(title, str) or not isinstance(content, str):
                raise ValueError("设置主题须提供标题和正文")
            updated = InnerTopic(section, identity, title.strip(), content.strip())
            if existing is None:
                topics.append(updated)
            else:
                topics[topics.index(existing)] = updated
        else:
            raise ValueError("未知主题修订操作")
    validate_topics(topics)
    return InnerDocument(topics, current.revision + (topics != current.topics), now)
