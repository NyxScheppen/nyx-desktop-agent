# pyright: reportPrivateUsage=false
import asyncio
import contextlib
import json
from collections.abc import AsyncGenerator
from typing import cast

import pytest

from nyx import db
from nyx.encounter.facade import EncounterFacade, _parse_encounter
from nyx.enums import (
    EmotionCategory,
    EncounterKind,
    EnergyState,
    EventType,
    OptionTone,
    Source,
)
from nyx.eval.evaluator import Evaluator
from nyx.events.bus import EventBus
from nyx.llm.client import LlmClient, LlmMessage
from nyx.types import CurrentState, Encounter, EncounterOption, Event, LLMOutput

_ENCOUNTER_JSON = json.dumps({
    "text": "夜晚，窗外的雨声敲着玻璃。",
    "options": [
        {"text": "走过去看看", "tone": "bold"},
        {"text": "先观察一下", "tone": "cautious"},
    ],
})


def _state(energy: float = 50.0) -> CurrentState:
    return CurrentState(
        valence=0.0, arousal=0.5, emotion=EmotionCategory.NEUTRAL,
        personality={"openness": 5.0, "conscientiousness": 5.0, "extraversion": 5.0,
                     "agreeableness": 5.0, "neuroticism": 5.0},
        values={"attitude_to_human": 5.0, "ai_identity_acceptance": 5.0,
                "altruism": 5.0, "optimism": 5.0},
        energy=energy, energy_state=EnergyState.OKAY,
        current_activity=None, active_desires=[],
    )


class _FakeLlm:
    def __init__(self, content: str = _ENCOUNTER_JSON) -> None:
        self.content = content

    async def complete(
        self,
        messages: list[LlmMessage],
        *,
        module: str,
        output_type: str,
        correlation_id: str,
        json_mode: bool = False,
    ) -> LLMOutput:
        return LLMOutput(
            id="llm1", module=module, type=output_type, model="fake",
            content=self.content, token_usage={"input": 1, "output": 1},
            correlation_id=correlation_id,
        )


class _RaisingLlm:
    async def complete(self, *args: object, **kwargs: object) -> LLMOutput:
        raise RuntimeError("boom")


class _FakeEvaluator:
    async def evaluate(self, output: LLMOutput) -> None:
        return None


def _make_facade(
    bus: EventBus,
    llm: object = None,
    state: CurrentState | None = None,
) -> EncounterFacade:
    async def get_state() -> CurrentState:
        return state if state is not None else _state()
    return EncounterFacade(
        bus,
        cast(LlmClient, llm or _FakeLlm()),
        cast(Evaluator, _FakeEvaluator()),
        get_state,
        canon="canon",
    )


def _enc(kind: EncounterKind = EncounterKind.RANDOM_EVENT) -> Encounter:
    return Encounter(
        id="enc1", kind=kind, text="开场",
        options=[EncounterOption(text="走过去", tone=OptionTone.BOLD)],
        correlation_id="c1", started_at=0.0,
    )


def _activity_end(completed: bool = True, type_: str = "reading") -> Event:
    content = {"type": type_, "result": {"completed": completed, "book": "骑士团史"}}
    return Event(
        id="e1", timestamp=0.0, source=Source.INTERNAL,
        type=EventType.ACTIVITY_END, content=content, correlation_id="c1",
    )


@contextlib.asynccontextmanager
async def _running(bus: EventBus) -> AsyncGenerator[None]:
    task = asyncio.create_task(bus.run())
    try:
        yield
        await asyncio.wait_for(bus._queue.join(), timeout=1.0)
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


def _subscribe(bus: EventBus, *types_: EventType) -> list[Event]:
    events: list[Event] = []
    async def record(event: Event) -> None:
        events.append(event)
    for t in types_:
        bus.subscribe(t, record)
    return events


# ---- _parse_encounter 纯函数 ----

def test_parse_encounter_valid() -> None:
    text, options = _parse_encounter(_ENCOUNTER_JSON)
    assert text == "夜晚，窗外的雨声敲着玻璃。"
    assert len(options) == 2
    assert options[0].tone is OptionTone.BOLD


def test_parse_encounter_missing_text() -> None:
    with pytest.raises(ValueError):
        _parse_encounter(json.dumps({"options": [{"text": "a", "tone": "bold"}]}))


def test_parse_encounter_too_few_options() -> None:
    with pytest.raises(ValueError):
        _parse_encounter(
            json.dumps({"text": "t", "options": [{"text": "a", "tone": "bold"}]})
        )


def test_parse_encounter_too_many_options() -> None:
    opts = [{"text": f"o{i}", "tone": "bold"} for i in range(5)]
    with pytest.raises(ValueError):
        _parse_encounter(json.dumps({"text": "t", "options": opts}))


def test_parse_encounter_bad_tone() -> None:
    with pytest.raises(ValueError):
        _parse_encounter(json.dumps({"text": "t", "options": [
            {"text": "a", "tone": "heroic"}, {"text": "b", "tone": "bold"},
        ]}))


def test_parse_encounter_option_not_dict() -> None:
    with pytest.raises(ValueError):
        _parse_encounter(json.dumps({"text": "t", "options": ["a", "b"]}))


# ---- choose ----

async def test_choose_applies_and_ends() -> None:
    database = await db.connect(":memory:")
    bus = EventBus(database)
    facade = _make_facade(bus)
    facade._current = _enc()
    choice_events = _subscribe(bus, EventType.ENCOUNTER_CHOICE)
    end_events = _subscribe(bus, EventType.ENCOUNTER_END)
    try:
        async with _running(bus):
            result = await facade.choose("enc1", 0)
        assert result is not None
        assert facade._current is None
        assert len(choice_events) == 1
        assert len(end_events) == 1
        end = end_events[0].content
        assert end["ending"] != ""
        assert end["consequences"]["energy_delta"] == -5.0
        assert "memory" not in end["consequences"]  # 随机事件不落记忆
    finally:
        await database.conn.close()


async def test_choose_growth_attaches_memory() -> None:
    database = await db.connect(":memory:")
    bus = EventBus(database)
    facade = _make_facade(bus)
    facade._current = _enc(EncounterKind.GROWTH_MOMENT)
    end_events = _subscribe(bus, EventType.ENCOUNTER_END)
    try:
        async with _running(bus):
            await facade.choose("enc1", 0)
        assert "memory" in end_events[0].content["consequences"]
    finally:
        await database.conn.close()


async def test_choose_wrong_id_returns_none() -> None:
    database = await db.connect(":memory:")
    bus = EventBus(database)
    facade = _make_facade(bus)
    facade._current = _enc()
    result = await facade.choose("other", 0)
    assert result is None
    assert facade._current is not None  # 保留
    await database.conn.close()


async def test_choose_bad_index_returns_none() -> None:
    database = await db.connect(":memory:")
    bus = EventBus(database)
    facade = _make_facade(bus)
    facade._current = _enc()
    result = await facade.choose("enc1", 5)
    assert result is None
    assert facade._current is not None
    await database.conn.close()


# ---- on_activity_end ----

async def test_on_activity_end_milestone() -> None:
    database = await db.connect(":memory:")
    bus = EventBus(database)
    facade = _make_facade(bus)
    started = _subscribe(bus, EventType.ENCOUNTER_START)
    try:
        async with _running(bus):
            await facade.on_activity_end(_activity_end(completed=True))
        assert facade._current is not None
        assert facade._current.kind is EncounterKind.GROWTH_MOMENT
        assert "book_finished" in facade._celebrated
        assert len(started) == 1
    finally:
        await database.conn.close()


async def test_on_activity_end_non_milestone() -> None:
    database = await db.connect(":memory:")
    bus = EventBus(database)
    facade = _make_facade(bus)
    await facade.on_activity_end(_activity_end(completed=True, type_="creation"))
    assert facade._current is None
    await database.conn.close()


async def test_start_llm_failure_no_crash() -> None:
    database = await db.connect(":memory:")
    bus = EventBus(database)
    facade = _make_facade(bus, llm=_RaisingLlm())
    await facade.on_activity_end(_activity_end(completed=True))  # 不抛
    assert facade._current is None
    await database.conn.close()


async def test_start_rooted_broadcasts_start() -> None:
    database = await db.connect(":memory:")
    bus = EventBus(database)
    facade = _make_facade(bus)
    started = _subscribe(bus, EventType.ENCOUNTER_START)
    try:
        async with _running(bus):
            await facade.start_rooted("争议观点", "量子退相干", "a1")
        current = facade.get_current()
        assert current is not None
        assert current["kind"] == "rooted"
        assert len(started) == 1
        assert facade._current is not None
        assert facade._current.kind is EncounterKind.ROOTED
    finally:
        await database.conn.close()


async def test_start_rooted_guarded_when_encounter_in_progress() -> None:
    """进行中的遭遇不被有根遭遇撞掉：_current 非空时 start_rooted 直接返回。"""
    database = await db.connect(":memory:")
    bus = EventBus(database)
    facade = _make_facade(bus)
    facade._current = _enc()  # 进行中的随机遭遇
    started = _subscribe(bus, EventType.ENCOUNTER_START)
    try:
        async with _running(bus):
            await facade.start_rooted("争议观点", "量子退相干", "a1")
        assert facade._current is not None
        assert facade._current.kind is EncounterKind.RANDOM_EVENT  # 未被替换
        assert len(started) == 0  # 未新增 ENCOUNTER_START
    finally:
        await database.conn.close()
