# pyright: reportPrivateUsage=false
from pathlib import Path

import pytest

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
    assert _load_canon(tmp_path) == "canon"


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
        personality = await store.get_personality()
        assert personality == {
            "openness": 8.0, "conscientiousness": 8.0, "extraversion": 2.0,
            "agreeableness": 6.0, "neuroticism": 7.0,
        }
        values = await store.get_values()
        assert values == {
            "attitude_to_human": 8.0, "ai_identity_acceptance": 6.0,
            "altruism": 9.0, "optimism": 5.0,
        }
        assert await store.get_energy() == (100.0, EnergyState.ENERGETIC)
        narrative = await store.get_narrative()
        assert narrative is not None
        assert narrative.identity == "我是模仿女主人公创造的 AI，希望能成为人类"
        await _seed_inner_life(store)
        assert await store.get_personality() == personality
        assert await store.get_values() == values
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
    names = {t["name"] for t in _build_tools(Config()).schema()}
    assert names == {"local_search", "file_io"}


def test_build_tools_web_enabled() -> None:
    cfg = Config(exploration=ExplorationConfig(web_enabled=True))
    names = {t["name"] for t in _build_tools(cfg).schema()}
    assert names == {"local_search", "file_io", "web_search"}
