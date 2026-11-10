# pyright: reportPrivateUsage=false
import json
from pathlib import Path

import pytest

from nyx.bootstrap import load_skills
from nyx.config import Config, ExplorationConfig
from nyx.db import connect
from nyx.desire.store import DesireStore
from nyx.enums import DesireType, EnergyState, EventType, Source
from nyx.inner_life.store import InnerLifeStore
from nyx.main import (
    _build_tools,
    _load_ask,
    _load_canon,
    _root_event,
    _seed_desire,
    _seed_inner_life,
)
from nyx.tools.registry import ToolRegistry
from nyx.types import Tool


def test_skill_startup_requires_both_skills_and_validates_actual_tools(
    tmp_path: Path,
) -> None:
    registry = ToolRegistry()

    async def extra_tool() -> str:
        return "ok"

    registry.register(Tool("extra_tool", "实际装配的工具", {}, extra_tool))
    (tmp_path / "discussion.json").write_text(json.dumps({
        "name": "discussion", "system_fragment": "研究策略",
        "tool_names": ["extra_tool"],
    }), encoding="utf-8")
    with pytest.raises(ValueError, match="缺少 conversation"):
        load_skills(tmp_path, registry)
    (tmp_path / "conversation.json").write_text(json.dumps({
        "name": "conversation", "system_fragment": "对话策略", "tool_names": [],
    }), encoding="utf-8")
    assert load_skills(tmp_path, registry)["discussion"].tool_names == ("extra_tool",)
    with pytest.raises(KeyError, match="extra_tool"):
        load_skills(tmp_path, ToolRegistry())

# ---- _root_event ----


def test_root_event_defaults_external() -> None:
    event = _root_event(EventType.USER_MESSAGE, {"message": "hi"})
    assert event.id == event.correlation_id
    assert event.source is Source.EXTERNAL
    assert event.type is EventType.USER_MESSAGE
    assert event.content == {"message": "hi"}
    assert event.timestamp > 0


def test_root_event_explicit_internal() -> None:
    event = _root_event(EventType.CLOCK_TICK, {"tick_type": "x"}, Source.INTERNAL)
    assert event.source is Source.INTERNAL


# ---- _load_canon ----


def test_load_canon_reads_canon_file(tmp_path: Path) -> None:
    (tmp_path / "canon.md").write_text("canon", encoding="utf-8")
    (tmp_path / "character-reference.md").write_text("reference", encoding="utf-8")
    assert _load_canon(tmp_path) == "canon\n\nreference"


def test_load_canon_missing_file_fails(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        _load_canon(tmp_path)


# ---- _load_ask ----


def test_load_ask_reads_ask_file(tmp_path: Path) -> None:
    (tmp_path / "ask.md").write_text("主动提问", encoding="utf-8")
    assert _load_ask(tmp_path) == "主动提问"


def test_load_ask_missing_file_fails(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        _load_ask(tmp_path)


# ---- _seed_inner_life ----


async def test_seed_inner_life_idempotent() -> None:
    database = await connect(":memory:")
    store = InnerLifeStore(database)
    try:
        await _seed_inner_life(store)
        assert await store.get_energy() == (100.0, EnergyState.ENERGETIC)
        narrative = await store.get_document()
        assert narrative is not None and narrative.revision == 1
        assert any(t.section == "identity" for t in narrative.topics)
        await _seed_inner_life(store)
        assert await store.get_document() == narrative
    finally:
        await database.conn.close()


# ---- _seed_desire ----


async def test_seed_desire_idempotent() -> None:
    database = await connect(":memory:")
    store = DesireStore(database)
    try:
        await _seed_desire(store)
        assert {dv.type for dv in await store.list_values()} == set(DesireType)
        assert len(await store.list_long_term()) == 3
        await _seed_desire(store)
        assert len(await store.list_values()) == 4
        assert len(await store.list_long_term()) == 3
    finally:
        await database.conn.close()


# ---- _build_tools ----


def test_build_tools_web_disabled() -> None:
    names = {
        t["function"]["name"]
        for t in _build_tools(Config()).schema()
    }
    assert names == {"local_search", "file_io"}


def test_build_tools_web_enabled() -> None:
    cfg = Config(exploration=ExplorationConfig(web_enabled=True))
    names = {
        t["function"]["name"]
        for t in _build_tools(cfg).schema()
    }
    assert names == {"local_search", "file_io", "web_search", "web_fetch"}
