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
from nyx.desire.lifecycle import (
    DesireLifecycle,
    _build_desire_prompt,
    _parse_desire,
    _topic_seed,
)
from nyx.desire.store import DesireStore
from nyx.desire.value import (
    REFUND_DELTA,
    SUPPRESSION_RAISE_DELTA,
    WEIGHT_REINFORCE_DELTA,
)
from nyx.enums import DesireStatus, DesireType, EventType, GoalAction, Source
from nyx.eval.evaluator import Evaluator
from nyx.events.bus import EventBus
from nyx.llm.client import LlmClient, LlmMessage
from nyx.types import (
    DesireValue,
    Event,
    Goal,
    LLMOutput,
    LongTermDesire,
    ShortTermDesire,
)

_DESIRE_JSON = json.dumps(
    {
        "description": "读一段骑士团的历史",
        "goal": {"action": "read", "count": 3, "topic": "骑士团"},
    }
)


def _desire(id: str, created_at: float = 1000.0) -> ShortTermDesire:
    return ShortTermDesire(
        id=id,
        created_at=created_at,
        type=DesireType.INTERACTION,
        strength=0.9,
        description="读骑士小说",
        goal=None,
    )


def _dv(
    type: DesireType,
    value: float,
    suppression: float = 0.5,
    updated_at: float = 1000.0,
) -> DesireValue:
    return DesireValue(
        type=type,
        value=value,
        expression_weight=0.7,
        suppression_threshold=suppression,
        updated_at=updated_at,
    )


def _lt(
    type: DesireType, subtopics: list[str] | None = None
) -> LongTermDesire:
    return LongTermDesire(
        id="lt1",
        created_at=1000.0,
        type=type,
        name="探索世界",
        description="了解骑士团历史",
        strength=0.5,
        progress=0.0,
        subtopics=subtopics if subtopics is not None else [],
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
            id=f"llm-{len(self.calls)}",
            module=module,
            type=output_type,
            model="fake",
            content=self._response,
            token_usage={"input": 1, "output": 1},
            correlation_id=correlation_id,
        )


class _FakeEvaluator:
    def __init__(self) -> None:
        self.evaluated: list[LLMOutput] = []

    async def evaluate(self, output: LLMOutput) -> None:
        self.evaluated.append(output)


def _make_lifecycle(
    store: DesireStore,
    bus: EventBus,
    llm: _FakeLlm,
    evaluator: _FakeEvaluator,
    config: DesireConfig | None = None,
) -> DesireLifecycle:
    return DesireLifecycle(
        store,
        bus,
        cast(LlmClient, llm),
        cast(Evaluator, evaluator),
        config if config is not None else DesireConfig(),
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


# ---- 纯函数 ----


def test_parse_desire() -> None:
    description, goal = _parse_desire(_DESIRE_JSON)
    assert description == "读一段骑士团的历史"
    assert goal == Goal(action=GoalAction.READ, count=3, topic="骑士团")
    assert _parse_desire('{"description": "x", "goal": null}') == ("x", None)
    with pytest.raises(ValueError):
        _parse_desire('{"goal": null}')                              # 缺 description
    with pytest.raises(ValueError):
        _parse_desire('{"description": "", "goal": null}')           # 空 description
    with pytest.raises(ValueError):
        _parse_desire('{"description": "x", "goal": {"action": "fly", "count": 1}}')
    with pytest.raises(ValueError):
        _parse_desire('{"description": "x", "goal": {"action": "read", "count": 0}}')
    with pytest.raises(ValueError):
        _parse_desire('{"description": "x", "goal": {"action": "read", "count": "3"}}')
    with pytest.raises(ValueError):
        _parse_desire(
            '{"description": "x", "goal": {"action": "read", "count": 1, "topic": 5}}'
        )
    with pytest.raises(ValueError):
        _parse_desire("[]")                                          # 非对象


def test_topic_seed() -> None:
    lt = _lt(DesireType.EXPLORATION, ["骑士团"])
    assert _topic_seed(DesireType.EXPLORATION, [lt]) == "骑士团"
    assert _topic_seed(DesireType.INTERACTION, [lt]) is None
    assert _topic_seed(DesireType.EXPLORATION, [_lt(DesireType.EXPLORATION)]) is None


def test_build_desire_prompt() -> None:
    prompt = _build_desire_prompt(DesireType.EXPLORATION, "骑士团")
    assert "exploration" in prompt
    assert "骑士团" in prompt
    assert "（无）" in _build_desire_prompt(DesireType.REST, None)


# ---- pressure_from_observation ----


async def test_pressure_from_observation() -> None:
    store, bus, database = await _new_stack()
    lifecycle = _make_lifecycle(store, bus, _FakeLlm(), _FakeEvaluator())
    try:
        event = Event(
            id="e1",
            timestamp=0.0,
            source=Source.EXTERNAL,
            type=EventType.OBSERVATION_STATE,
            content={},
            correlation_id="c1",
        )
        await lifecycle.pressure_from_observation(event)
        dv = await store.get_value(DesireType.INTERACTION)
        assert dv is not None
        assert dv.value == pytest.approx(0.15)      # default 0 → +0.15
        assert dv.updated_at > 0.0
    finally:
        await database.conn.close()


# ---- run_eval ----


async def test_run_eval_no_peak() -> None:
    store, bus, database = await _new_stack()
    llm = _FakeLlm()
    lifecycle = _make_lifecycle(store, bus, llm, _FakeEvaluator())
    try:
        async with _running(bus):
            result = await lifecycle.run_eval()
        assert result == []
        assert llm.calls == []
    finally:
        await database.conn.close()


async def test_run_eval_generates_peak(monkeypatch: pytest.MonkeyPatch) -> None:
    store, bus, database = await _new_stack()
    llm = _FakeLlm()
    evaluator = _FakeEvaluator()
    lifecycle = _make_lifecycle(store, bus, llm, evaluator)
    events = _subscribe(bus)
    try:
        t0 = 1_000_000.0
        monkeypatch.setattr("nyx.desire.lifecycle.time.time", lambda: t0)
        await store.upsert_value(_dv(DesireType.INTERACTION, 0.9, updated_at=t0))
        async with _running(bus):
            result = await lifecycle.run_eval()
        assert len(result) == 1
        desire = result[0]
        assert desire.type is DesireType.INTERACTION
        assert desire.status is DesireStatus.PENDING
        assert desire.strength == pytest.approx(0.9)
        assert desire.description == "读一段骑士团的历史"
        assert desire.goal == Goal(action=GoalAction.READ, count=3, topic="骑士团")
        assert llm.calls == ["desire"]
        assert [o.type for o in evaluator.evaluated] == ["desire"]
        [generated] = [e for e in events if e.type is EventType.DESIRE_GENERATED]
        assert generated.content["desire_id"] == desire.id
        dv = await store.get_value(DesireType.INTERACTION)
        assert dv is not None and dv.value == pytest.approx(0.0)   # 重置
    finally:
        await database.conn.close()


async def test_run_eval_only_most_urgent(monkeypatch: pytest.MonkeyPatch) -> None:
    store, bus, database = await _new_stack()
    llm = _FakeLlm()
    lifecycle = _make_lifecycle(store, bus, llm, _FakeEvaluator())
    try:
        t0 = 1_000_000.0
        monkeypatch.setattr("nyx.desire.lifecycle.time.time", lambda: t0)
        await store.upsert_value(_dv(DesireType.INTERACTION, 0.9, updated_at=t0))
        await store.upsert_value(_dv(DesireType.EXPLORATION, 0.85, updated_at=t0))
        async with _running(bus):
            result = await lifecycle.run_eval()
        assert [d.type for d in result] == [DesireType.INTERACTION]
        dv = await store.get_value(DesireType.EXPLORATION)
        assert dv is not None and dv.value == pytest.approx(0.85)   # 保留不重置
    finally:
        await database.conn.close()


async def test_run_eval_long_term_pressure(monkeypatch: pytest.MonkeyPatch) -> None:
    store, bus, database = await _new_stack()
    llm = _FakeLlm()
    lifecycle = _make_lifecycle(store, bus, llm, _FakeEvaluator())
    try:
        t0 = 1_000_000.0
        monkeypatch.setattr("nyx.desire.lifecycle.time.time", lambda: t0)
        await store.upsert_value(_dv(DesireType.EXPLORATION, 0.5, updated_at=t0))
        await store.insert_long_term(_lt(DesireType.EXPLORATION))
        async with _running(bus):
            result = await lifecycle.run_eval()
        assert result == []                       # 0.5 + 0.2 = 0.7 < 0.8 未达峰
        dv = await store.get_value(DesireType.EXPLORATION)
        assert dv is not None and dv.value == pytest.approx(0.7)
    finally:
        await database.conn.close()


async def test_run_eval_decay(monkeypatch: pytest.MonkeyPatch) -> None:
    store, bus, database = await _new_stack()
    llm = _FakeLlm()
    lifecycle = _make_lifecycle(store, bus, llm, _FakeEvaluator())
    try:
        t0 = 1_000_000.0
        monkeypatch.setattr("nyx.desire.lifecycle.time.time", lambda: t0)
        await store.upsert_value(
            _dv(DesireType.INTERACTION, 0.5, updated_at=t0 - 86400.0)
        )
        async with _running(bus):
            result = await lifecycle.run_eval()
        assert result == []
        dv = await store.get_value(DesireType.INTERACTION)
        assert dv is not None and dv.value == pytest.approx(0.5 - 0.02)
    finally:
        await database.conn.close()


async def test_run_eval_suppression_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    store, bus, database = await _new_stack()
    llm = _FakeLlm()
    lifecycle = _make_lifecycle(store, bus, llm, _FakeEvaluator())
    try:
        t0 = 1_000_000.0
        monkeypatch.setattr("nyx.desire.lifecycle.time.time", lambda: t0)
        await store.upsert_value(
            _dv(DesireType.INTERACTION, 0.85, suppression=0.9, updated_at=t0)
        )
        async with _running(bus):
            result = await lifecycle.run_eval()
        assert result == []                       # 达峰但被抑制
        assert llm.calls == []
    finally:
        await database.conn.close()


async def test_run_eval_topic_seed(monkeypatch: pytest.MonkeyPatch) -> None:
    store, bus, database = await _new_stack()
    llm = _FakeLlm()
    lifecycle = _make_lifecycle(store, bus, llm, _FakeEvaluator())
    try:
        t0 = 1_000_000.0
        monkeypatch.setattr("nyx.desire.lifecycle.time.time", lambda: t0)
        await store.upsert_value(_dv(DesireType.EXPLORATION, 0.9, updated_at=t0))
        await store.insert_long_term(_lt(DesireType.EXPLORATION, ["骑士团"]))
        async with _running(bus):
            await lifecycle.run_eval()
        assert "骑士团" in llm.user_contents[0]
    finally:
        await database.conn.close()


# ---- satisfy / expire ----


async def test_satisfy_goal_met(monkeypatch: pytest.MonkeyPatch) -> None:
    store, bus, database = await _new_stack()
    lifecycle = _make_lifecycle(store, bus, _FakeLlm(), _FakeEvaluator())
    events = _subscribe(bus)
    try:
        t0 = 1_000_000.0
        monkeypatch.setattr("nyx.desire.lifecycle.time.time", lambda: t0)
        await store.upsert_value(_dv(DesireType.INTERACTION, 0.5, updated_at=t0))
        await store.insert_long_term(_lt(DesireType.INTERACTION))
        await store.add_desire(_desire("d1"))
        async with _running(bus):
            await lifecycle.satisfy("d1", True)
        d = await store.get_desire("d1")
        assert d is not None and d.status is DesireStatus.SATISFIED
        dv = await store.get_value(DesireType.INTERACTION)
        assert dv is not None
        assert dv.expression_weight == pytest.approx(0.7 + WEIGHT_REINFORCE_DELTA)
        lt = (await store.list_long_term())[0]
        assert lt.progress == pytest.approx(0.1)
        [satisfied] = [e for e in events if e.type is EventType.DESIRE_SATISFIED]
        assert satisfied.content["desire_id"] == "d1"
    finally:
        await database.conn.close()


async def test_satisfy_goal_progress(monkeypatch: pytest.MonkeyPatch) -> None:
    """goal 精确计数：goal.count 次 goal_met 才满足，中间保持 PENDING 累计进度。"""
    store, bus, database = await _new_stack()
    lifecycle = _make_lifecycle(store, bus, _FakeLlm(), _FakeEvaluator())
    try:
        t0 = 1_000_000.0
        monkeypatch.setattr("nyx.desire.lifecycle.time.time", lambda: t0)
        desire = _desire("d1")
        desire.goal = Goal(GoalAction.READ, 3, "骑士团")
        await store.add_desire(desire)
        async with _running(bus):
            await lifecycle.satisfy("d1", True)
            await lifecycle.satisfy("d1", True)
        d = await store.get_desire("d1")
        assert d is not None
        assert d.goal_progress == 2
        assert d.status is DesireStatus.PENDING
        async with _running(bus):
            await lifecycle.satisfy("d1", True)
        d = await store.get_desire("d1")
        assert d is not None
        assert d.status is DesireStatus.SATISFIED
        assert d.goal_progress == 3
    finally:
        await database.conn.close()


async def test_satisfy_retry() -> None:
    store, bus, database = await _new_stack()
    lifecycle = _make_lifecycle(store, bus, _FakeLlm(), _FakeEvaluator())
    events = _subscribe(bus)
    try:
        desire = _desire("d1")
        desire.retry_count = 1
        await store.add_desire(desire)
        async with _running(bus):
            await lifecycle.satisfy("d1", False)
        d = await store.get_desire("d1")
        assert d is not None and d.retry_count == 2
        assert d.status is DesireStatus.PENDING
        assert events == []                       # 无事件
    finally:
        await database.conn.close()


async def test_satisfy_retry_exceeds_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    store, bus, database = await _new_stack()
    lifecycle = _make_lifecycle(store, bus, _FakeLlm(), _FakeEvaluator())
    events = _subscribe(bus)
    try:
        t0 = 1_000_000.0
        monkeypatch.setattr("nyx.desire.lifecycle.time.time", lambda: t0)
        await store.upsert_value(_dv(DesireType.INTERACTION, 0.3, updated_at=t0))
        desire = _desire("d1")
        desire.retry_count = 3                    # +1 后 4 > retry_limit(3)
        await store.add_desire(desire)
        async with _running(bus):
            await lifecycle.satisfy("d1", False)
        d = await store.get_desire("d1")
        assert d is not None and d.status is DesireStatus.EXPIRED
        dv = await store.get_value(DesireType.INTERACTION)
        assert dv is not None and dv.value == pytest.approx(0.3 + REFUND_DELTA)
        assert dv.suppression_threshold == pytest.approx(0.5 + SUPPRESSION_RAISE_DELTA)
        [expired] = [e for e in events if e.type is EventType.DESIRE_EXPIRED]
        assert expired.content["desire_id"] == "d1"
    finally:
        await database.conn.close()


async def test_expire(monkeypatch: pytest.MonkeyPatch) -> None:
    store, bus, database = await _new_stack()
    lifecycle = _make_lifecycle(store, bus, _FakeLlm(), _FakeEvaluator())
    events = _subscribe(bus)
    try:
        t0 = 1_000_000.0
        monkeypatch.setattr("nyx.desire.lifecycle.time.time", lambda: t0)
        await store.upsert_value(_dv(DesireType.INTERACTION, 0.3, updated_at=t0))
        await store.add_desire(_desire("d1"))
        async with _running(bus):
            await lifecycle.expire("d1")
        d = await store.get_desire("d1")
        assert d is not None and d.status is DesireStatus.EXPIRED
        dv = await store.get_value(DesireType.INTERACTION)
        assert dv is not None and dv.value == pytest.approx(0.3 + REFUND_DELTA)
        [expired] = [e for e in events if e.type is EventType.DESIRE_EXPIRED]
        assert expired.content["desire_id"] == "d1"
    finally:
        await database.conn.close()


async def test_satisfy_expire_missing() -> None:
    store, bus, database = await _new_stack()
    lifecycle = _make_lifecycle(store, bus, _FakeLlm(), _FakeEvaluator())
    events = _subscribe(bus)
    try:
        async with _running(bus):
            await lifecycle.satisfy("nonexistent", True)
            await lifecycle.expire("nonexistent")
        assert events == []                       # 无事件、不抛
    finally:
        await database.conn.close()


# ---- run_eval LLM 兜底 ----


async def test_run_eval_llm_invalid_json_skips(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, bus, database = await _new_stack()
    llm = _FakeLlm("not json")
    lifecycle = _make_lifecycle(store, bus, llm, _FakeEvaluator())
    try:
        t0 = 1_000_000.0
        monkeypatch.setattr("nyx.desire.lifecycle.time.time", lambda: t0)
        await store.upsert_value(_dv(DesireType.INTERACTION, 0.9, updated_at=t0))
        async with _running(bus):
            result = await lifecycle.run_eval()
        assert result == []                       # 非法 JSON 不抛，跳过本次
        dv = await store.get_value(DesireType.INTERACTION)
        assert dv is not None and dv.value == pytest.approx(0.9)   # 目标不重置
        assert await store.list_pending() == []    # 无欲望入队
    finally:
        await database.conn.close()


async def test_run_eval_evaluator_error_propagates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, bus, database = await _new_stack()
    llm = _FakeLlm()

    class _BoomEvaluator:
        async def evaluate(self, output: LLMOutput) -> None:
            raise RuntimeError("boom")

    lifecycle = DesireLifecycle(
        store,
        bus,
        cast(LlmClient, llm),
        cast(Evaluator, _BoomEvaluator()),
        DesireConfig(),
    )
    try:
        t0 = 1_000_000.0
        monkeypatch.setattr("nyx.desire.lifecycle.time.time", lambda: t0)
        await store.upsert_value(_dv(DesireType.INTERACTION, 0.9, updated_at=t0))
        with pytest.raises(RuntimeError):
            async with _running(bus):
                await lifecycle.run_eval()
    finally:
        await database.conn.close()


# ---- satisfy/expire 幂等 ----


async def test_satisfy_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    store, bus, database = await _new_stack()
    lifecycle = _make_lifecycle(store, bus, _FakeLlm(), _FakeEvaluator())
    events = _subscribe(bus)
    try:
        t0 = 1_000_000.0
        monkeypatch.setattr("nyx.desire.lifecycle.time.time", lambda: t0)
        await store.upsert_value(_dv(DesireType.INTERACTION, 0.5, updated_at=t0))
        await store.add_desire(_desire("d1"))
        async with _running(bus):
            await lifecycle.satisfy("d1", True)
            await lifecycle.satisfy("d1", True)
        dv = await store.get_value(DesireType.INTERACTION)
        assert dv is not None
        assert dv.expression_weight == pytest.approx(0.7 + WEIGHT_REINFORCE_DELTA)
        assert len([e for e in events if e.type is EventType.DESIRE_SATISFIED]) == 1
    finally:
        await database.conn.close()


async def test_expire_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    store, bus, database = await _new_stack()
    lifecycle = _make_lifecycle(store, bus, _FakeLlm(), _FakeEvaluator())
    events = _subscribe(bus)
    try:
        t0 = 1_000_000.0
        monkeypatch.setattr("nyx.desire.lifecycle.time.time", lambda: t0)
        await store.upsert_value(_dv(DesireType.INTERACTION, 0.3, updated_at=t0))
        await store.add_desire(_desire("d1"))
        async with _running(bus):
            await lifecycle.expire("d1")
            await lifecycle.expire("d1")
        dv = await store.get_value(DesireType.INTERACTION)
        assert dv is not None
        assert dv.value == pytest.approx(0.3 + REFUND_DELTA)       # 只回灌一次
        assert dv.suppression_threshold == pytest.approx(0.5 + SUPPRESSION_RAISE_DELTA)
        assert len([e for e in events if e.type is EventType.DESIRE_EXPIRED]) == 1
    finally:
        await database.conn.close()
