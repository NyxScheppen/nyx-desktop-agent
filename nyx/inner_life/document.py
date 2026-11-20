"""The current inner document and exact, evidence-backed topic revisions."""

import re
from collections.abc import Mapping
from dataclasses import asdict
from typing import Any, cast

from nyx.policy import policy
from nyx.types import InnerDocument, InnerTopic

SECTIONS = policy("inner_life", "document.SECTIONS", dict[str, str])
MAX_DOCUMENT_CHARS = policy("inner_life", "document.MAX_DOCUMENT_CHARS", int)
MAX_TOPIC_CHARS = policy("inner_life", "document.MAX_TOPIC_CHARS", int)
MAX_TOPICS = policy("inner_life", "document.MAX_TOPICS", int)


def validate_topics(topics: list[InnerTopic]) -> None:
    """Reject ambiguous anchors and invalid/oversized current documents."""
    if not topics or len(topics) > MAX_TOPICS:
        raise ValueError(
            policy("inner_life", "document.copy.validate_topics_text", str)
        )
    seen: set[str] = set()
    for topic in topics:
        if topic.section not in SECTIONS:
            raise ValueError(
                policy("inner_life", "document.copy.validate_topics_text_3", str)
            )
        if not re.fullmatch(r"[a-z][a-z0-9-]{0,79}", topic.id) or topic.id in seen:
            raise ValueError(
                policy("inner_life", "document.copy.validate_topics_text_4", str)
            )
        seen.add(topic.id)
        if not topic.title.strip() or len(topic.title) > policy(
            "inner_life", "document.parameters.validate_topics_value_limit", int
        ):
            raise ValueError(
                policy("inner_life", "document.copy.validate_topics_text_5", str)
            )
        if not topic.content.strip() or len(topic.content) > MAX_TOPIC_CHARS:
            raise ValueError(
                policy("inner_life", "document.copy.validate_topics_text_6", str)
            )
    if (
        sum(len(t.content) + len(t.title) + len(t.id) for t in topics)
        > MAX_DOCUMENT_CHARS
    ):
        raise ValueError(
            policy("inner_life", "document.copy.validate_topics_text_2", str)
        )


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
        raise ValueError(
            policy("inner_life", "document.copy.topics_from_data_text", str)
        )
    topics: list[InnerTopic] = []
    for value in cast(list[Any], data):
        if not isinstance(value, dict):
            raise ValueError(
                policy("inner_life", "document.copy.topics_from_data_text_2", str)
            )
        item = cast(dict[str, Any], value)
        if set(item) != {"section", "id", "title", "content"} or any(
            not isinstance(v, str) for v in item.values()
        ):
            raise ValueError(
                policy("inner_life", "document.copy.topics_from_data_text_3", str)
            )
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
    if not isinstance(patches, list) or len(cast(list[Any], patches)) > policy(
        "inner_life", "document.parameters.revise_document_value_limit", int
    ):
        raise ValueError(
            policy("inner_life", "document.copy.revise_document_text", str)
        )
    topics = [InnerTopic(**asdict(t)) for t in current.topics]
    seen: set[str] = set()
    for raw in cast(list[Any], patches):
        if not isinstance(raw, dict):
            raise ValueError(
                policy("inner_life", "document.copy.revise_document_text_2", str)
            )
        patch = cast(dict[str, Any], raw)
        section, identity = patch.get("section"), patch.get("id")
        if (
            not isinstance(identity, str)
            or identity in seen
            or not isinstance(section, str)
            or section not in SECTIONS
        ):
            raise ValueError(
                policy("inner_life", "document.copy.revise_document_text_3", str)
            )
        seen.add(identity)
        reason = patch.get("reason")
        refs = patch.get("evidence")
        if (
            not isinstance(reason, str)
            or not reason.strip()
            or not isinstance(refs, list)
            or not refs
        ):
            raise ValueError(
                policy("inner_life", "document.copy.revise_document_text_4", str)
            )
        for reference in cast(list[Any], refs):
            if not isinstance(reference, dict):
                raise ValueError(
                    policy("inner_life", "document.copy.revise_document_text_6", str)
                )
            ref = cast(dict[str, Any], reference)
            source, quote = ref.get("id"), ref.get("quote")
            if (
                not isinstance(source, str)
                or source not in evidence
                or not isinstance(quote, str)
                or not quote.strip()
                or quote not in evidence[source]
            ):
                raise ValueError(
                    policy("inner_life", "document.copy.revise_document_text_7", str)
                )
        existing = next((t for t in topics if t.id == identity), None)
        if existing is not None and existing.section != section:
            raise ValueError(
                policy("inner_life", "document.copy.revise_document_text_5", str)
            )
        operation = patch.get("op")
        if operation == "remove":
            if existing is None:
                raise ValueError(
                    policy("inner_life", "document.copy.revise_document_text_8", str)
                )
            topics.remove(existing)
        elif operation == "set":
            title, content = patch.get("title"), patch.get("content")
            if not isinstance(title, str) or not isinstance(content, str):
                raise ValueError(
                    policy("inner_life", "document.copy.revise_document_text_10", str)
                )
            updated = InnerTopic(section, identity, title.strip(), content.strip())
            if existing is None:
                topics.append(updated)
            else:
                topics[topics.index(existing)] = updated
        else:
            raise ValueError(
                policy("inner_life", "document.copy.revise_document_text_9", str)
            )
    validate_topics(topics)
    return InnerDocument(topics, current.revision + (topics != current.topics), now)
