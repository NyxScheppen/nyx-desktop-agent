# pyright: reportPrivateUsage=false
import asyncio
import contextlib
import json
from collections.abc import AsyncGenerator
from typing import cast

import pytest

from nyx import db
from nyx.config import DesireConfig
from nyx.db import Database
from nyx.desire.facade import DesireFacade
from nyx.desire.store import DesireStore
from nyx.enums import DesireStatus, DesireType, EventType, Source
from nyx.eval.evaluator import Evaluator
from nyx.events.bus import EventBus
from nyx.llm.client import LlmClient, LlmMessage
from nyx.types import (
    DesireValue,
    Event,
    LLMOutput,
    LongTermDesire,
    Memory,
    ShortTermDesire,
)

_DESIRE_JSON = json.dumps(
    {
        "description": "读一段骑士团的历史",
        "goal": {"action": "read", "count": 3, "topic": "骑士团"},
    }
)


def _desire(id: str, status: DesireStatus = DesireStatus.PENDING) -> ShortTermDesire:
    return ShortTermDesire(
        id=id,
        created_at=1000.0,
        type=DesireType.INTERACTION,
        strength=0.9,
        description="读骑士小说",
        goal=None,
        status=status,
    )


def _dv(type: DesireType, value: float, updated_at: float = 1000.0) -> DesireValue:
    return DesireValue(
        type=type,
        value=value,
        expression_weight=0.7,
        suppression_threshold=0.5,
        updated_at=updated_at,
    )


def _lt(type: DesireType) -> LongTermDesire:
    return LongTermDesire(
        id="lt1",
        created_at=1000.0,
        type=type,
        name="探索世界",
        description="了解骑士团历史",
        strength=0.5,
        progress=0.0,
        subtopics=["骑士团"],
    )


class _FakeLlm:
    """complete 按 output_type="desire" 返回 fixture JSON，记录调用。"""

    def __init__(self, response: str = _DESIRE_JSON) -> None:
        self._response = response
        self.calls: list[str] = []
        self.user_contents: list[str] = []

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
        self.user_contents.append(messages[1]["content"])
        return LLMOutput(
            module=module,
            type=output_type,
            model="fake",
            content=self._response,
            correlation_id=correlation_id,
        )


class _FakeEvaluator:
    def __init__(self) -> None:
        self.evaluated: list[LLMOutput] = []

    async def evaluate(self, output: LLMOutput) -> None:
        self.evaluated.append(output)


def _make_facade(
    store: DesireStore,
    bus: EventBus,
    llm: _FakeLlm,
    evaluator: _FakeEvaluator,
    config: DesireConfig | None = None,
) -> DesireFacade:
    async def list_memories() -> list[Memory]:
        return []

    return DesireFacade(
        store,
        bus,
        cast(LlmClient, llm),
        cast(Evaluator, evaluator),
        config if config is not None else DesireConfig(),
        list_memories,
    )


async def _new_stack() -> tuple[DesireStore, EventBus, Database]:
    database = await db.connect(":memory:")
    return DesireStore(database), EventBus(database), database


def _subscribe(bus: EventBus) -> list[Event]:
    events: list[Event] = []

    async def record(event: Event) -> None:
        events.append(event)

    for t in (
        EventType.DESIRE_GENERATED,
        EventType.DESIRE_SATISFIED,
        EventType.DESIRE_EXPIRED,
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


# ---- add_value ----


async def test_add_value_observation() -> None:
    store, bus, database = await _new_stack()
    facade = _make_facade(store, bus, _FakeLlm(), _FakeEvaluator())
    try:
        event = Event(
            id="e1",
            timestamp=0.0,
            source=Source.EXTERNAL,
            type=EventType.OBSERVATION_STATE,
            content={},
            correlation_id="c1",
        )
        await facade.add_value(event)
        dv = await store.get_value(DesireType.INTERACTION)
        assert dv is not None
        assert dv.value == pytest.approx(0.15)      # 互动欲加压
    finally:
        await database.conn.close()


async def test_add_value_activity_end_satisfies() -> None:
    store, bus, database = await _new_stack()
    facade = _make_facade(store, bus, _FakeLlm(), _FakeEvaluator())
    events = _subscribe(bus)
    try:
        await store.add_desire(_desire("d1"))
        event = Event(
            id="e1",
            timestamp=0.0,
            source=Source.EXTERNAL,
            type=EventType.ACTIVITY_END,
            content={"desire_id": "d1", "goal_met": True},
            correlation_id="c1",
        )
        async with _running(bus):
            await facade.add_value(event)
        d = await store.get_desire("d1")
        assert d is not None and d.status is DesireStatus.SATISFIED
        assert [e for e in events if e.type is EventType.DESIRE_SATISFIED]
    finally:
        await database.conn.close()


async def test_add_value_activity_end_ignores_invalid() -> None:
    store, bus, database = await _new_stack()
    facade = _make_facade(store, bus, _FakeLlm(), _FakeEvaluator())
    try:
        await store.add_desire(_desire("d1"))
        # 缺 goal_met
        await facade.add_value(
            Event(
                id="e1",
                timestamp=0.0,
                source=Source.EXTERNAL,
                type=EventType.ACTIVITY_END,
                content={"desire_id": "d1"},
                correlation_id="c1",
            )
        )
        d = await store.get_desire("d1")
        assert d is not None and d.status is DesireStatus.PENDING
        # goal_met 类型错
        await facade.add_value(
            Event(
                id="e2",
                timestamp=0.0,
                source=Source.EXTERNAL,
                type=EventType.ACTIVITY_END,
                content={"desire_id": "d1", "goal_met": "yes"},
                correlation_id="c2",
            )
        )
        d = await store.get_desire("d1")
        assert d is not None and d.status is DesireStatus.PENDING
    finally:
        await database.conn.close()


# ---- evaluate / get_pending / get_all ----


async def test_evaluate_and_getters(monkeypatch: pytest.MonkeyPatch) -> None:
    store, bus, database = await _new_stack()
    facade = _make_facade(store, bus, _FakeLlm(), _FakeEvaluator())
    try:
        t0 = 1_000_000.0
        monkeypatch.setattr("nyx.desire.lifecycle.time.time", lambda: t0)
        await store.upsert_value(_dv(DesireType.INTERACTION, 0.9, updated_at=t0))
        async with _running(bus):
            result = await facade.evaluate()
        assert len(result) == 1
        pending = await facade.get_pending()
        assert [d.id for d in pending] == [result[0].id]
        state = await facade.get_all()
        assert len(state.values) == 4
        assert [d.id for d in state.short_term] == [result[0].id]
        assert state.long_term == []
    finally:
        await database.conn.close()


async def test_get_all_snapshot() -> None:
    store, bus, database = await _new_stack()
    facade = _make_facade(store, bus, _FakeLlm(), _FakeEvaluator())
    try:
        await store.add_desire(_desire("d1", status=DesireStatus.SATISFIED))
        await store.upsert_value(_dv(DesireType.INTERACTION, 0.5))
        await store.insert_long_term(_lt(DesireType.EXPLORATION))
        state = await facade.get_all()
        assert [d.id for d in state.short_term] == ["d1"]   # 含 satisfied 历史
        assert [x.id for x in state.long_term] == ["lt1"]
        assert len(state.values) == 1
    finally:
        await database.conn.close()


# ---- satisfy / expire / add_long_term ----


async def test_satisfy_expire_delegate() -> None:
    store, bus, database = await _new_stack()
    facade = _make_facade(store, bus, _FakeLlm(), _FakeEvaluator())
    try:
        await store.add_desire(_desire("d1"))
        await facade.satisfy("d1", True)
        d = await store.get_desire("d1")
        assert d is not None and d.status is DesireStatus.SATISFIED
        await store.add_desire(_desire("d2"))
        await facade.expire("d2")
        d2 = await store.get_desire("d2")
        assert d2 is not None and d2.status is DesireStatus.EXPIRED
    finally:
        await database.conn.close()


async def test_mark_active_suppressed_delegate() -> None:
    store, bus, database = await _new_stack()
    facade = _make_facade(store, bus, _FakeLlm(), _FakeEvaluator())
    try:
        await store.add_desire(_desire("d1"))
        await facade.mark_active("d1")
        d = await store.get_desire("d1")
        assert d is not None and d.status is DesireStatus.ACTIVE
        await facade.mark_suppressed("d1")
        d = await store.get_desire("d1")
        assert d is not None and d.status is DesireStatus.SUPPRESSED
    finally:
        await database.conn.close()


async def test_add_long_term_delegates() -> None:
    store, bus, database = await _new_stack()
    facade = _make_facade(store, bus, _FakeLlm(), _FakeEvaluator())
    try:
        lt = _lt(DesireType.EXPLORATION)
        await facade.add_long_term(lt)
        assert await store.list_long_term() == [lt]
    finally:
        await database.conn.close()
