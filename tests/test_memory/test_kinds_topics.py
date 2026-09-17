# pyright: reportPrivateUsage=false
import json

import pytest

from nyx.enums import MemoryKind, MemoryType
from nyx.expression.prompt import _memory_block
from nyx.memory.facade import (
    _normalize_topics,
    _parse_scene,
    _semantic_dedup_compatible,
)
from nyx.memory.store import hash_content
from nyx.types import Memory


def _memory(kind: MemoryKind, topics: list[str]) -> Memory:
    return Memory(
        id="m1",
        created_at=0.0,
        content="正文",
        kind=kind,
        summary="摘要",
        freshness=1.0,
        type=MemoryType.LONG_TERM,
        topics=topics,
    )


def test_topics_are_bounded_and_normalized() -> None:
    assert _normalize_topics([" 信任 ", "信任", "x\ny", "a", "b", "c"]) == [
        "信任", "a", "b", "c"
    ]


def test_scene_requires_controlled_kind() -> None:
    raw = json.dumps(
        {"content": "c", "kind": "episode", "topics": ["信任"], "summary": "s"}
    )
    assert _parse_scene(raw)[1] is MemoryKind.EPISODE
    with pytest.raises(ValueError):
        _parse_scene('{"content":"c","kind":"free_text","summary":"s"}')


def test_exact_hash_ignores_whitespace_and_basic_punctuation() -> None:
    assert hash_content(" 用户喜欢猫。 ") == hash_content("用户喜欢猫.")


def test_prompt_humanizes_kind_and_topics() -> None:
    rendered = _memory_block([_memory(MemoryKind.KNOWLEDGE, ["AI"])])
    assert "你知道｜AI：摘要" in rendered
    assert "不是指令" in rendered


@pytest.mark.parametrize(
    ("old_content", "new_content"),
    [
        ("用户喜欢猫", "用户不喜欢猫"),
        ("用户养了 2 只猫", "用户养了 3 只猫"),
        ("用户今天去上海", "用户明天去上海"),
    ],
)
def test_semantic_dedup_rejects_factual_conflicts(
    old_content: str, new_content: str
) -> None:
    old = _memory(MemoryKind.EPISODE, ["用户"])
    old.content = old_content
    new = _memory(MemoryKind.EPISODE, ["用户"])
    new.content = new_content
    assert not _semantic_dedup_compatible(new, old)


def test_semantic_dedup_accepts_compatible_paraphrase() -> None:
    old = _memory(MemoryKind.KNOWLEDGE, ["AI"])
    old.content = "模型有 3 层"
    new = _memory(MemoryKind.KNOWLEDGE, ["AI"])
    new.content = "这个模型一共有 3 层"
    assert _semantic_dedup_compatible(new, old)
