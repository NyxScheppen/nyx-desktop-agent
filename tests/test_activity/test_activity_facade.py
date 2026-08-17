# pyright: reportPrivateUsage=false
import asyncio
import contextlib
import json
from collections.abc import AsyncGenerator
from typing import Any, cast

import pytest

from nyx import db
from nyx.activity.facade import (
    ActivityFacade,
    _current_hour,
    _day_start,
    _goal_met,
    _parse_activity_result,
)
from nyx.activity.store import ActivityStore
from nyx.config import ActivityConfig, ExplorationConfig
from nyx.db import Database
from nyx.desire.facade import DesireFacade
from nyx.enums import (
    ActivityStatus,
    ActivityType,
    DesireStatus,
    DesireType,
    EmotionCategory,
    EnergyState,
    EventType,
    GoalAction,
    Source,
)
from nyx.eval.evaluator import Evaluator
from nyx.events.bus import EventBus
from nyx.llm.client import LlmClient, LlmMessage
from nyx.memory.facade import MemoryFacade
from nyx.tools.registry import ToolRegistry
from nyx.types import (
    Activity,
    CurrentState,
    DesireState,
    DesireValue,
    Event,
    Goal,
    LLMOutput,
    Memory,
    Personality,
    ShortTermDesire,
    Values,
)

_PERSONALITY: Personality = {
    "openness": 5.0,
    "conscientiousness": 5.0,
    "extraversion": 5.0,
    "agreeableness": 5.0,
    "neuroticism": 5.0,
}

_VALUES: Values = {
    "attitude_to_human": 5.0,
    "ai_identity_acceptance": 5.0,
    "altruism": 5.0,
    "optimism": 5.0,
}

_READING_JSON = json.dumps({"book": "骑士团历史", "note": "读到了第三章"})
_CREATION_JSON = json.dumps({"title": "小狐狸的日记", "content": "今天也努力了"})
_PLAN_JSON = json.dumps({"focus": "骑士团", "done": False})


def _mk_state(energy: float) -> CurrentState:
    return CurrentState(
        valence=0.0,
        arousal=0.0,
        emotion=EmotionCategory.NEUTRAL,
        personality=_PERSONALITY,
        values=_VALUES,
        energy=energy,
        energy_state=EnergyState.OKAY,
        current_activity=None,
        active_desires=[],
    )


def _desire(
    id: str,
    type_: DesireType,
    description: str = "读骑士小说",
    goal: Goal | None = None,
) -> ShortTermDesire:
    return ShortTermDesire(
        id=id,
        created_at=1000.0,
        type=type_,
        strength=0.9,
        description=description,
        goal=goal,
        status=DesireStatus.PENDING,
    )


def _activity(
    id: str,
    type_: ActivityType = ActivityType.READING,
    status: ActivityStatus = ActivityStatus.PENDING,
    started_at: float = 1000.0,
) -> Activity:
    return Activity(
        id=id,
        type=type_,
        schedule_block_id="09:00",
        status=status,
        progress={"desire_id": None, "goal": None, "correlation_id": None},
        started_at=started_at,
    )


class _FakeLlm:
    def __init__(self) -> None:
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
        content = {
            "reading": _READING_JSON,
            "creation": _CREATION_JSON,
            "exploration_plan": _PLAN_JSON,
        }.get(output_type, "{}")
        return LLMOutput(
            id=f"llm-{len(self.calls)}",
            module=module,
            type=output_type,
            model="fake",
            content=content,
            token_usage={"input": 1, "output": 1},
            correlation_id=correlation_id,
        )


class _FakeEvaluator:
    def __init__(self) -> None:
        self.evaluated: list[LLMOutput] = []

    async def evaluate(self, output: LLMOutput) -> None:
        self.evaluated.append(output)


class _FakeDesire:
    def __init__(
        self,
        pending: list[ShortTermDesire] | None = None,
        values: list[DesireValue] | None = None,
    ) -> None:
        self._pending = pending if pending is not None else []
        self._values = values if values is not None else []

    async def get_pending(self) -> list[ShortTermDesire]:
        return self._pending

    async def get_all(self) -> DesireState:
        return DesireState(
            values=self._values, short_term=self._pending, long_term=[]
        )


class _FakeTools:
    async def call(self, name: str, args: dict[str, Any]) -> Any:
        if name in ("local_search", "web_search"):
            return ["一条检索结果"]
        return "文件内容"


class _FakeMemory:
    async def search(self, query: str) -> list[Memory]:
        return []


async def _new_facade(
    pending: list[ShortTermDesire] | None = None,
    values: list[DesireValue] | None = None,
    energy: float = 80.0,
    llm: _FakeLlm | None = None,
    evaluator: _FakeEvaluator | None = None,
) -> tuple[ActivityFacade, ActivityStore, EventBus, Database]:
    database = await db.connect(":memory:")
    store = ActivityStore(database)
    bus = EventBus(database)

    async def get_state() -> CurrentState:
        return _mk_state(energy)

    facade = ActivityFacade(
        store,
        bus,
        cast(LlmClient, llm if llm is not None else _FakeLlm()),
        cast(Evaluator, evaluator if evaluator is not None else _FakeEvaluator()),
        cast(ToolRegistry, _FakeTools()),
        cast(MemoryFacade, _FakeMemory()),
        cast(DesireFacade, _FakeDesire(pending, values)),
        get_state,
        ActivityConfig(),
        ExplorationConfig(),
    )
    return facade, store, bus, database


def _subscribe_activity(bus: EventBus) -> list[Event]:
    events: list[Event] = []

    async def record(event: Event) -> None:
        events.append(event)

    for t in (
        EventType.ACTIVITY_START,
        EventType.ACTIVITY_END,
        EventType.ACTIVITY_INTERRUPTED,
    ):
        bus.subscribe(t, record)
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


async def _await_task(facade: ActivityFacade) -> None:
    task = facade._task
    assert task is not None
    await task


# ---- 纯函数 ----


def test_day_start() -> None:
    assert _day_start(86400.0 * 1.5) == 86400.0


def test_current_hour() -> None:
    assert _current_hour(5400.0) == 1.5


def test_goal_met() -> None:
    assert _goal_met(None, {}) is None
    assert _goal_met({"action": "read"}, {}) is False
    assert _goal_met({"action": "read"}, {"book": "x"}) is True


def test_parse_activity_result_valid() -> None:
    assert _parse_activity_result(
        json.dumps({"book": "b", "note": "n"}), "reading"
    ) == {"book": "b", "note": "n"}
    assert _parse_activity_result(
        json.dumps({"title": "t", "content": "c"}), "creation"
    ) == {"title": "t", "content": "c"}


def test_parse_activity_result_missing_key_raises() -> None:
    with pytest.raises(ValueError):
        _parse_activity_result(json.dumps({"book": "b"}), "reading")


def test_parse_activity_result_non_dict_raises() -> None:
    with pytest.raises(ValueError):
        _parse_activity_result("[1, 2, 3]", "reading")


# ---- select_activity ----


async def test_select_activity_empty() -> None:
    facade, _store, _bus, database = await _new_facade()
    try:
        assert facade.select_activity([], _mk_state(80.0)) is None
    finally:
        await database.conn.close()


async def test_select_activity_exploration() -> None:
    facade, _store, _bus, database = await _new_facade()
    try:
        d = _desire(
            "d1", DesireType.EXPLORATION, goal=Goal(GoalAction.READ, 3, "骑士团")
        )
        act = facade.select_activity([d], _mk_state(80.0))
        assert act is not None
        assert act.type is ActivityType.READING
        assert act.progress["desire_id"] == "d1"
        assert act.progress["description"] == d.description
        assert act.progress["goal"] == {
            "action": "read", "count": 3, "topic": "骑士团",
        }
    finally:
        await database.conn.close()


async def test_select_activity_interaction_returns_none() -> None:
    facade, _store, _bus, database = await _new_facade()
    try:
        d = _desire("d1", DesireType.INTERACTION)
        assert facade.select_activity([d], _mk_state(80.0)) is None
    finally:
        await database.conn.close()


async def test_select_activity_rest_desire() -> None:
    facade, _store, _bus, database = await _new_facade()
    try:
        d = _desire("d1", DesireType.REST)
        act = facade.select_activity([d], _mk_state(80.0))
        assert act is not None
        assert act.type is ActivityType.REST
        assert act.progress["desire_id"] == "d1"
    finally:
        await database.conn.close()


async def test_select_activity_low_energy_rest() -> None:
    facade, _store, _bus, database = await _new_facade()
    try:
        d = _desire("d1", DesireType.EXPLORATION)
        act = facade.select_activity([d], _mk_state(30.0))
        assert act is not None
        assert act.type is ActivityType.REST
        assert act.progress["desire_id"] is None
    finally:
        await database.conn.close()


# ---- 生命周期 ----


async def test_maybe_start_skips_when_running() -> None:
    facade, store, _bus, database = await _new_facade(
        pending=[_desire("d1", DesireType.EXPLORATION)], energy=80.0
    )
    try:
        await store.insert(_activity("run", status=ActivityStatus.RUNNING))
        await facade._maybe_start_activity()
        acts = await store.list_schedule(0.0)
        assert [a.id for a in acts] == ["run"]
    finally:
        await database.conn.close()


async def test_default_idle_reflection_when_tired() -> None:
    facade, store, bus, database = await _new_facade(energy=30.0)
    try:
        async with _running(bus):
            await facade._maybe_start_activity()
            await _await_task(facade)
        acts = await store.list_schedule(0.0)
        assert len(acts) == 1
        assert acts[0].type is ActivityType.IDLE_REFLECTION
        assert acts[0].progress["desire_id"] is None
    finally:
        await database.conn.close()


async def test_default_observe_user_when_energetic() -> None:
    facade, store, bus, database = await _new_facade(energy=80.0)
    try:
        async with _running(bus):
            await facade._maybe_start_activity()
            await _await_task(facade)
        acts = await store.list_schedule(0.0)
        assert len(acts) == 1
        assert acts[0].type is ActivityType.OBSERVE_USER
        assert acts[0].progress["desire_id"] is None
    finally:
        await database.conn.close()


async def test_maybe_start_creation_activity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    t0 = 1_000_000.0
    monkeypatch.setattr("nyx.activity.facade.time.time", lambda: t0)
    llm = _FakeLlm()
    evaluator = _FakeEvaluator()
    facade, _store, bus, database = await _new_facade(
        pending=[_desire("d1", DesireType.CREATION)],
        energy=80.0,
        llm=llm,
        evaluator=evaluator,
    )
    try:
        events = _subscribe_activity(bus)
        async with _running(bus):
            await facade._maybe_start_activity()
            await _await_task(facade)
        starts = [e for e in events if e.type is EventType.ACTIVITY_START]
        ends = [e for e in events if e.type is EventType.ACTIVITY_END]
        assert len(starts) == 1
        assert starts[0].source is Source.INTERNAL
        assert ends[0].content["desire_id"] == "d1"
        assert ends[0].content["energy_delta"] == -25
        assert len(evaluator.evaluated) == 1
    finally:
        await database.conn.close()


async def test_upgrade_to_free_exploration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    t0 = 1_000_000.0
    monkeypatch.setattr("nyx.activity.facade.time.time", lambda: t0)
    facade, store, bus, database = await _new_facade(
        pending=[_desire("d1", DesireType.EXPLORATION)], energy=80.0
    )
    try:
        async with _running(bus):
            await facade._maybe_start_activity()
            await _await_task(facade)
        acts = await store.list_schedule(0.0)
        assert len(acts) == 1
        assert acts[0].type is ActivityType.FREE_EXPLORATION
    finally:
        await database.conn.close()


async def test_no_upgrade_when_rate_limited(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    t0 = 1_000_000.0
    monkeypatch.setattr("nyx.activity.facade.time.time", lambda: t0)
    facade, store, bus, database = await _new_facade(
        pending=[_desire("d1", DesireType.EXPLORATION)], energy=80.0
    )
    try:
        await store.insert(
            _activity("prev", type_=ActivityType.FREE_EXPLORATION, started_at=t0)
        )
        async with _running(bus):
            await facade._maybe_start_activity()
            await _await_task(facade)
        acts = await store.list_schedule(0.0)
        new = [a for a in acts if a.id != "prev"]
        assert len(new) == 1
        assert new[0].type is ActivityType.READING
    finally:
        await database.conn.close()


async def test_complete_activity() -> None:
    facade, store, bus, database = await _new_facade()
    try:
        events = _subscribe_activity(bus)
        a = _activity(
            "a1", type_=ActivityType.READING, status=ActivityStatus.RUNNING
        )
        await store.insert(a)
        async with _running(bus):
            await facade.complete_activity(a)
        got = await store.get("a1")
        assert got is not None
        assert got.status is ActivityStatus.COMPLETED
        assert got.ended_at is not None
        ends = [e for e in events if e.type is EventType.ACTIVITY_END]
        assert len(ends) == 1
        assert ends[0].content["energy_delta"] == -20
    finally:
        await database.conn.close()


async def test_interrupt_running() -> None:
    facade, store, bus, database = await _new_facade()
    try:
        events = _subscribe_activity(bus)
        await store.insert(_activity("a1", status=ActivityStatus.RUNNING))
        async with _running(bus):
            await facade.interrupt("a1", EventType.USER_MESSAGE)
        got = await store.get("a1")
        assert got is not None
        assert got.status is ActivityStatus.PAUSED
        ints = [e for e in events if e.type is EventType.ACTIVITY_INTERRUPTED]
        assert len(ints) == 1
        assert ints[0].content["by"] == "user_message"
    finally:
        await database.conn.close()


async def test_interrupt_missing() -> None:
    facade, _store, bus, database = await _new_facade()
    try:
        events = _subscribe_activity(bus)
        async with _running(bus):
            await facade.interrupt("nope", EventType.USER_MESSAGE)
        assert events == []
    finally:
        await database.conn.close()


async def test_get_current_delegates() -> None:
    facade, store, _bus, database = await _new_facade()
    try:
        await store.insert(_activity("a1", status=ActivityStatus.RUNNING))
        cur = await facade.get_current()
        assert cur is not None
        assert cur.id == "a1"
    finally:
        await database.conn.close()


async def test_get_schedule_delegates(monkeypatch: pytest.MonkeyPatch) -> None:
    t0 = 1_000_000.0
    monkeypatch.setattr("nyx.activity.facade.time.time", lambda: t0)
    facade, store, _bus, database = await _new_facade()
    try:
        await store.insert(_activity("a1", started_at=t0))
        acts = await facade.get_schedule()
        assert [a.id for a in acts] == ["a1"]
    finally:
        await database.conn.close()
