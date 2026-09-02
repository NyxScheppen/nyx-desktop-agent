# pyright: reportPrivateUsage=false
import asyncio
import contextlib
import json
from collections.abc import AsyncGenerator
from typing import Any, cast

import pytest

from nyx import db
from nyx.activity.facade import ActivityFacade
from nyx.config import Config
from nyx.db import Database
from nyx.desire.facade import DesireFacade
from nyx.enums import (
    ActivityStatus,
    ActivityType,
    DesireType,
    EnergyState,
    EventType,
    Source,
)
from nyx.eval.evaluator import Evaluator
from nyx.events.bus import EventBus
from nyx.inner_life.facade import InnerLifeFacade
from nyx.inner_life.store import InnerLifeStore
from nyx.llm.client import LlmClient, LlmMessage
from nyx.memory.facade import MemoryFacade
from nyx.types import (
    Activity,
    Aesthetic,
    DesireState,
    Event,
    LLMOutput,
    LongTermDesire,
    Memory,
    Personality,
    SelfNarrative,
    ShortTermDesire,
    Values,
)

_REFLECTION_JSON = json.dumps(
    {
        "story": "今天对用户了解更多",
        "becoming": "我更愿意探索了",
        "self_view": {"自信": "稍强"},
        "personality_delta": {},
        "values_delta": {},
        "long_term_desires": [],
    }
)

_PERSONALITY: Personality = {
    "openness": 8.0,
    "conscientiousness": 8.0,
    "extraversion": 2.0,
    "agreeableness": 6.0,
    "neuroticism": 7.0,
}

_VALUES: Values = {
    "attitude_to_human": 8.0,
    "ai_identity_acceptance": 6.0,
    "altruism": 9.0,
    "optimism": 5.0,
}

_AESTHETIC: Aesthetic = {
    "ornate": 7.0,
    "lyrical": 7.0,
    "classical": 6.0,
    "somber": 6.0,
}

_NARRATIVE = SelfNarrative(
    identity="尼克斯",
    story=["初始故事"],
    self_view={"自信": "中等"},
    becoming=["初始认知"],
    updated_at=1000.0,
)


class _FakeLlm:
    def __init__(self, response: str = _REFLECTION_JSON) -> None:
        self._response = response
        self.calls: list[str] = []
        self.correlation_ids: list[str] = []

    async def complete(
        self,
        messages: list[LlmMessage],
        *,
        module: str,
        output_type: str,
        correlation_id: str,
        json_mode: bool = False,
    ) -> LLMOutput:
        self.calls.append(output_type)
        self.correlation_ids.append(correlation_id)
        return LLMOutput(
            module=module,
            type=output_type,
            model="fake",
            content=self._response,
            correlation_id=correlation_id,
        )


class _FakeEvaluator:
    async def evaluate(self, output: LLMOutput) -> None:
        return None


class _FakeActivityFacade:
    def __init__(self, current: Activity | None = None) -> None:
        self._current = current

    async def get_current(self) -> Activity | None:
        return self._current


class _FakeDesireFacade:
    def __init__(self, pending: list[ShortTermDesire] | None = None) -> None:
        self._pending = pending if pending is not None else []

    async def get_pending(self) -> list[ShortTermDesire]:
        return self._pending

    async def get_all(self) -> DesireState:
        return DesireState(values=[], short_term=self._pending, long_term=[])

    async def add_long_term(self, desire: LongTermDesire) -> None:
        return None

    async def pressure_creation(self, delta: float) -> None:
        return None


class _FakeMemoryFacade:
    async def list_memories(self, tag: str | None = None) -> list[Memory]:
        del tag
        return []

    async def count_new(self, tag: str, since: float) -> int:
        del tag, since
        return 0


async def _seed(store: InnerLifeStore) -> None:
    await store.upsert_personality(_PERSONALITY)
    await store.upsert_values(_VALUES)
    await store.upsert_aesthetic(_AESTHETIC)
    await store.upsert_energy(100.0, EnergyState.ENERGETIC)


async def _new_facade(
    llm: _FakeLlm,
    evaluator: _FakeEvaluator,
    activity: Activity | None = None,
    pending: list[ShortTermDesire] | None = None,
) -> tuple[InnerLifeFacade, InnerLifeStore, EventBus, Database]:
    database = await db.connect(":memory:")
    store = InnerLifeStore(database)
    bus = EventBus(database)
    facade = InnerLifeFacade(
        store,
        cast(ActivityFacade, _FakeActivityFacade(activity)),
        cast(DesireFacade, _FakeDesireFacade(pending)),
        cast(MemoryFacade, _FakeMemoryFacade()),
        bus,
        cast(LlmClient, llm),
        cast(Evaluator, evaluator),
        Config(),
    )
    return facade, store, bus, database


def _event(
    type_: EventType,
    correlation_id: str = "c1",
    content: dict[str, Any] | None = None,
) -> Event:
    return Event(
        id="e1",
        timestamp=0.0,
        source=Source.INTERNAL,
        type=type_,
        content=content if content is not None else {},
        correlation_id=correlation_id,
    )


def _subscribe_emotion(bus: EventBus) -> list[Event]:
    events: list[Event] = []

    async def record(event: Event) -> None:
        events.append(event)

    bus.subscribe(EventType.EMOTION_UPDATE, record)
    return events


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


# ---- apply_event ----

async def test_apply_event_desire_satisfied(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    t0 = 1_000_000.0
    monkeypatch.setattr("nyx.inner_life.facade.time.time", lambda: t0)
    facade, store, bus, database = await _new_facade(_FakeLlm(), _FakeEvaluator())
    try:
        await _seed(store)
        events = _subscribe_emotion(bus)
        async with _running(bus):
            await facade.apply_event(_event(EventType.DESIRE_SATISFIED, "c1"))
        assert facade._valence == pytest.approx(0.2)
        assert facade._arousal == pytest.approx(0.1)
        assert len(events) == 1
        e = events[0]
        assert e.source is Source.INTERNAL and e.correlation_id == "c1"
        assert isinstance(e.content["emotion"], str)
    finally:
        await database.conn.close()


async def test_apply_event_activity_end(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    t0 = 1_000_000.0
    monkeypatch.setattr("nyx.inner_life.facade.time.time", lambda: t0)
    facade, store, bus, database = await _new_facade(_FakeLlm(), _FakeEvaluator())
    try:
        await _seed(store)
        async with _running(bus):
            await facade.apply_event(
                _event(EventType.ACTIVITY_END, content={"energy_delta": -25})
            )
        energy = await store.get_energy()
        assert energy is not None
        assert energy[0] == pytest.approx(75.0)
        assert energy[1] is EnergyState.OKAY
    finally:
        await database.conn.close()


async def test_apply_event_activity_end_no_delta(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    t0 = 1_000_000.0
    monkeypatch.setattr("nyx.inner_life.facade.time.time", lambda: t0)
    facade, store, bus, database = await _new_facade(_FakeLlm(), _FakeEvaluator())
    try:
        await _seed(store)
        async with _running(bus):
            await facade.apply_event(_event(EventType.ACTIVITY_END))
        energy = await store.get_energy()
        assert energy is not None and energy[0] == pytest.approx(100.0)
    finally:
        await database.conn.close()


async def test_apply_event_unseeded_energy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    t0 = 1_000_000.0
    monkeypatch.setattr("nyx.inner_life.facade.time.time", lambda: t0)
    facade, _store, bus, database = await _new_facade(_FakeLlm(), _FakeEvaluator())
    try:
        async with _running(bus):
            with pytest.raises(RuntimeError):
                await facade.apply_event(_event(EventType.DESIRE_SATISFIED))
            with pytest.raises(RuntimeError):
                await facade.apply_event(_event(EventType.ACTIVITY_END))
    finally:
        await database.conn.close()


async def test_apply_event_reflection(monkeypatch: pytest.MonkeyPatch) -> None:
    t0 = 1_000_000.0
    monkeypatch.setattr("nyx.inner_life.facade.time.time", lambda: t0)
    llm = _FakeLlm()
    facade, store, bus, database = await _new_facade(llm, _FakeEvaluator())
    try:
        await _seed(store)
        await store.upsert_narrative(_NARRATIVE)
        async with _running(bus):
            await facade.apply_event(_event(EventType.DESIRE_SATISFIED, "c0"))
        async with _running(bus):
            await facade.apply_event(_event(EventType.REFLECTION, "c1"))
        assert llm.calls == ["reflection"]
        assert llm.correlation_ids == ["c1"]
        assert facade._valence == pytest.approx(0.2)
        assert facade._arousal == pytest.approx(0.0)
    finally:
        await database.conn.close()


async def test_decay_settlement(monkeypatch: pytest.MonkeyPatch) -> None:
    now = [1_000_000.0]
    monkeypatch.setattr("nyx.inner_life.facade.time.time", lambda: now[0])
    facade, store, bus, database = await _new_facade(_FakeLlm(), _FakeEvaluator())
    try:
        await _seed(store)
        async with _running(bus):
            await facade.apply_event(_event(EventType.DESIRE_SATISFIED))
        now[0] += 86400.0
        async with _running(bus):
            await facade.apply_event(_event(EventType.OBSERVATION_STATE))
        assert facade._valence == pytest.approx(0.1)
    finally:
        await database.conn.close()


# ---- get_state / get_narrative / reflect ----


async def test_get_state(monkeypatch: pytest.MonkeyPatch) -> None:
    t0 = 1_000_000.0
    monkeypatch.setattr("nyx.inner_life.facade.time.time", lambda: t0)
    activity = Activity(
        id="a1",
        type=ActivityType.READING,
        schedule_block_id="b1",
        status=ActivityStatus.RUNNING,
        progress={},
        started_at=t0,
    )
    pending = [
        ShortTermDesire(
            id="d1",
            created_at=t0,
            type=DesireType.INTERACTION,
            strength=0.9,
            description="读骑士小说",
            goal=None,
        )
    ]
    facade, store, _bus, database = await _new_facade(
        _FakeLlm(), _FakeEvaluator(), activity=activity, pending=pending
    )
    try:
        await _seed(store)
        state = await facade.get_state()
        assert state.current_activity is ActivityType.READING
        assert state.active_desires == pending
        assert state.personality == _PERSONALITY
        assert state.aesthetic == _AESTHETIC
        assert state.energy == 100.0
        assert state.energy_state is EnergyState.ENERGETIC
    finally:
        await database.conn.close()


async def test_get_state_unseeded() -> None:
    facade, _store, _bus, database = await _new_facade(_FakeLlm(), _FakeEvaluator())
    try:
        with pytest.raises(RuntimeError):
            await facade.get_state()
    finally:
        await database.conn.close()


async def test_get_narrative() -> None:
    facade, store, _bus, database = await _new_facade(_FakeLlm(), _FakeEvaluator())
    try:
        with pytest.raises(RuntimeError):
            await facade.get_narrative()
        await store.upsert_narrative(_NARRATIVE)
        assert await facade.get_narrative() == _NARRATIVE
    finally:
        await database.conn.close()


async def test_reflect_delegation() -> None:
    llm = _FakeLlm()
    facade, store, _bus, database = await _new_facade(llm, _FakeEvaluator())
    try:
        await _seed(store)
        await store.upsert_narrative(_NARRATIVE)
        await facade.reflect("cid")
        assert llm.calls == ["reflection"]
        assert llm.correlation_ids == ["cid"]
    finally:
        await database.conn.close()


async def test_reflect_publishes_reflection_done() -> None:
    llm = _FakeLlm()
    facade, store, bus, database = await _new_facade(llm, _FakeEvaluator())
    try:
        await _seed(store)
        await store.upsert_narrative(_NARRATIVE)
        events: list[Event] = []

        async def record(event: Event) -> None:
            events.append(event)

        bus.subscribe(EventType.REFLECTION_DONE, record)
        async with _running(bus):
            outcome = await facade.reflect("cid")
        assert outcome is not None
        assert outcome.story_is_new is True
        assert len(events) == 1
        assert events[0].type is EventType.REFLECTION_DONE
        assert events[0].content == {
            "story": "今天对用户了解更多",
            "story_is_new": True,
        }
    finally:
        await database.conn.close()
