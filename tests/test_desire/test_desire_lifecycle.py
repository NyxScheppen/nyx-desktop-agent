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
    _most_relevant_long_term,
    _parse_desire,
    _pick_topic_seed,
    _subtopic_freshness,
    _subtopics_for,
)
from nyx.desire.store import DesireStore
from nyx.desire.value import (
    REFUND_DELTA,
    SUPPRESSION_RAISE_DELTA,
    WEIGHT_REINFORCE_DELTA,
)
from nyx.enums import (
    DesireStatus,
    DesireType,
    EventType,
    GoalAction,
    MemoryType,
    Source,
)
from nyx.eval.evaluator import Evaluator
from nyx.events.bus import EventBus
from nyx.llm.client import LlmClient, LlmMessage
from nyx.memory.retrieval import EmbedFn
from nyx.types import (
    DesireValue,
    Event,
    Goal,
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
    type: DesireType,
    subtopics: list[str] | None = None,
    id: str = "lt1",
) -> LongTermDesire:
    return LongTermDesire(
        id=id,
        created_at=1000.0,
        type=type,
        name="探索世界",
        description="了解骑士团历史",
        strength=0.5,
        progress=0.0,
        subtopics=subtopics if subtopics is not None else [],
    )


def _mem(summary: str = "x", content: str = "y", freshness: float = 1.0) -> Memory:
    return Memory(
        id="m1",
        created_at=1000.0,
        content=content,
        tag="reading",
        summary=summary,
        freshness=freshness,
        type=MemoryType.SHORT_TERM,
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


def _make_lifecycle(
    store: DesireStore,
    bus: EventBus,
    llm: _FakeLlm,
    evaluator: _FakeEvaluator,
    config: DesireConfig | None = None,
    memories: list[Memory] | None = None,
    embed: EmbedFn | None = None,
) -> DesireLifecycle:
    async def list_memories() -> list[Memory]:
        return memories if memories is not None else []

    return DesireLifecycle(
        store,
        bus,
        cast(LlmClient, llm),
        cast(Evaluator, evaluator),
        config if config is not None else DesireConfig(),
        list_memories,
        embed,
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


def test_subtopics_for() -> None:
    lt = _lt(DesireType.EXPLORATION, ["骑士团"])
    assert _subtopics_for(DesireType.EXPLORATION, [lt]) == ["骑士团"]
    assert _subtopics_for(DesireType.INTERACTION, [lt]) == []
    assert _subtopics_for(DesireType.EXPLORATION, [_lt(DesireType.EXPLORATION)]) == []


def test_subtopics_for_filters_blank() -> None:
    lt = _lt(DesireType.EXPLORATION, ["骑士团", "", "  ", "大学朋友"])
    assert _subtopics_for(DesireType.EXPLORATION, [lt]) == ["骑士团", "大学朋友"]


def test_subtopic_freshness_blank_not_wildcard() -> None:
    # 空串子主题曾是 substring 通配符（"" in s 恒 True）→ 应返回 None 不匹配
    memories = [_mem(summary="关于痛苦", freshness=0.9)]
    assert _subtopic_freshness("", memories) is None
    assert _subtopic_freshness("   ", memories) is None
    assert _subtopic_freshness("痛苦", memories) == pytest.approx(0.9)


def test_pick_topic_seed() -> None:
    assert _pick_topic_seed([], []) is None                       # 空池
    assert _pick_topic_seed(["痛苦", "死亡"], []) == "痛苦"        # 全没做过 → 第一个
    # 部分做过 → 取没做过的
    assert _pick_topic_seed(
        ["痛苦", "死亡"], [_mem(summary="关于痛苦", freshness=0.9)]
    ) == "死亡"
    # 都做过 → 取新鲜度最低（最久没碰）
    assert _pick_topic_seed(
        ["痛苦", "死亡"],
        [_mem(summary="关于痛苦", freshness=0.9),
         _mem(summary="关于死亡", freshness=0.2)],
    ) == "死亡"


def test_most_relevant_long_term() -> None:
    a = _lt(DesireType.EXPLORATION, ["骑士团"])
    b = _lt(DesireType.EXPLORATION, ["大学朋友"])
    # 无 type 匹配 → None
    assert _most_relevant_long_term(
        DesireType.INTERACTION, None, [a, b]
    ) is None
    # topic 双向 substring 命中第二条 → 回写第二条
    assert _most_relevant_long_term(
        DesireType.EXPLORATION, "大学朋友", [a, b]
    ) is b
    # topic 轻微漂移仍命中（"写骑士团同人" 含 "骑士团"）
    assert _most_relevant_long_term(
        DesireType.EXPLORATION, "写骑士团同人", [a, b]
    ) is a
    # topic=None → 第一个 type 匹配
    assert _most_relevant_long_term(
        DesireType.EXPLORATION, None, [a, b]
    ) is a
    # 同类型都不命中 → 第一个
    assert _most_relevant_long_term(
        DesireType.EXPLORATION, "别的主题", [a, b]
    ) is a


def test_most_relevant_long_term_blank_not_wildcard() -> None:
    # 空串子主题曾是 substring 通配符（"" in topic 恒 True）→ 应跳过，命中真实子主题
    a = _lt(DesireType.EXPLORATION, [""])
    b = _lt(DesireType.EXPLORATION, ["骑士团"])
    assert _most_relevant_long_term(DesireType.EXPLORATION, "骑士团", [a, b]) is b


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
        await store.upsert_value(_dv(DesireType.INTERACTION, 0.95, updated_at=t0))
        await store.upsert_value(_dv(DesireType.EXPLORATION, 0.92, updated_at=t0))
        async with _running(bus):
            result = await lifecycle.run_eval()
        assert [d.type for d in result] == [DesireType.INTERACTION]
        dv = await store.get_value(DesireType.EXPLORATION)
        assert dv is not None and dv.value == pytest.approx(0.92)   # 保留不重置
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
        assert result == []                       # 0.5 + 0.1 = 0.6 < 0.9 未达峰
        dv = await store.get_value(DesireType.EXPLORATION)
        assert dv is not None and dv.value == pytest.approx(0.6)
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
        assert dv is not None and dv.value == pytest.approx(0.5 - 0.05)
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
            _dv(DesireType.INTERACTION, 0.92, suppression=0.95, updated_at=t0)
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
    lifecycle = _make_lifecycle(
        store, bus, llm, _FakeEvaluator(),
        memories=[_mem(summary="关于骑士团", freshness=0.9)],
    )
    try:
        t0 = 1_000_000.0
        monkeypatch.setattr("nyx.desire.lifecycle.time.time", lambda: t0)
        await store.upsert_value(_dv(DesireType.EXPLORATION, 0.9, updated_at=t0))
        await store.insert_long_term(
            _lt(DesireType.EXPLORATION, ["骑士团", "大学朋友"])
        )
        async with _running(bus):
            await lifecycle.run_eval()
        assert "大学朋友" in llm.user_contents[0]   # 骑士团有记忆（做过）→ 取没做过的
        assert "骑士团" not in llm.user_contents[0]
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


async def test_satisfy_reinforces_most_relevant_long_term(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """同类型两条长期欲望 + goal.topic 命中第二条 → 只回写第二条 progress。"""
    store, bus, database = await _new_stack()
    lifecycle = _make_lifecycle(store, bus, _FakeLlm(), _FakeEvaluator())
    try:
        t0 = 1_000_000.0
        monkeypatch.setattr("nyx.desire.lifecycle.time.time", lambda: t0)
        await store.upsert_value(_dv(DesireType.EXPLORATION, 0.5, updated_at=t0))
        await store.insert_long_term(_lt(DesireType.EXPLORATION, ["骑士团"], id="lt1"))
        await store.insert_long_term(
            _lt(DesireType.EXPLORATION, ["大学朋友"], id="lt2")
        )
        desire = _desire("d1")
        desire.type = DesireType.EXPLORATION
        desire.goal = Goal(GoalAction.READ, 1, "大学朋友")
        await store.add_desire(desire)
        async with _running(bus):
            await lifecycle.satisfy("d1", True)
        by_id = {lt.id: lt for lt in await store.list_long_term()}
        assert by_id["lt1"].progress == pytest.approx(0.0)   # 未命中，不动
        assert by_id["lt2"].progress == pytest.approx(0.1)   # 命中，回写
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


# ---- mark_active / mark_suppressed ----


async def test_mark_active_pending_to_active() -> None:
    store, bus, database = await _new_stack()
    lifecycle = _make_lifecycle(store, bus, _FakeLlm(), _FakeEvaluator())
    try:
        await store.add_desire(_desire("d1"))
        await lifecycle.mark_active("d1")
        d = await store.get_desire("d1")
        assert d is not None and d.status is DesireStatus.ACTIVE
    finally:
        await database.conn.close()


async def test_mark_active_guard() -> None:
    store, bus, database = await _new_stack()
    lifecycle = _make_lifecycle(store, bus, _FakeLlm(), _FakeEvaluator())
    try:
        for status in (
            DesireStatus.SUPPRESSED,
            DesireStatus.SATISFIED,
            DesireStatus.EXPIRED,
        ):
            d = _desire(f"d-{status.value}")
            d.status = status
            await store.add_desire(d)
            await lifecycle.mark_active(d.id)
            got = await store.get_desire(d.id)
            assert got is not None and got.status is status   # 非 PENDING 不转
        await lifecycle.mark_active("missing")               # 缺失 no-op 不抛
    finally:
        await database.conn.close()


async def test_mark_suppressed_active_to_suppressed() -> None:
    store, bus, database = await _new_stack()
    lifecycle = _make_lifecycle(store, bus, _FakeLlm(), _FakeEvaluator())
    try:
        d = _desire("d1")
        d.status = DesireStatus.ACTIVE
        await store.add_desire(d)
        await lifecycle.mark_suppressed("d1")
        got = await store.get_desire("d1")
        assert got is not None and got.status is DesireStatus.SUPPRESSED
    finally:
        await database.conn.close()


async def test_mark_suppressed_guard() -> None:
    store, bus, database = await _new_stack()
    lifecycle = _make_lifecycle(store, bus, _FakeLlm(), _FakeEvaluator())
    try:
        for status in (
            DesireStatus.PENDING,
            DesireStatus.SATISFIED,
            DesireStatus.EXPIRED,
        ):
            d = _desire(f"d-{status.value}")
            d.status = status
            await store.add_desire(d)
            await lifecycle.mark_suppressed(d.id)
            got = await store.get_desire(d.id)
            assert got is not None and got.status is status   # 非 ACTIVE 不转
        await lifecycle.mark_suppressed("missing")           # 缺失 no-op 不抛
    finally:
        await database.conn.close()


async def test_satisfy_releases_active() -> None:
    store, bus, database = await _new_stack()
    lifecycle = _make_lifecycle(store, bus, _FakeLlm(), _FakeEvaluator())
    try:
        # 未达标：ACTIVE → PENDING（不卡 ACTIVE）
        d = _desire("d1")
        d.status = DesireStatus.ACTIVE
        await store.add_desire(d)
        await lifecycle.satisfy("d1", False)
        got = await store.get_desire("d1")
        assert got is not None and got.status is DesireStatus.PENDING
        assert got.retry_count == 1
        # 达标：ACTIVE → SATISFIED
        d2 = _desire("d2")
        d2.status = DesireStatus.ACTIVE
        await store.add_desire(d2)
        await lifecycle.satisfy("d2", True)
        got2 = await store.get_desire("d2")
        assert got2 is not None and got2.status is DesireStatus.SATISFIED
    finally:
        await database.conn.close()


async def test_run_eval_releases_suppressed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, bus, database = await _new_stack()
    llm = _FakeLlm()
    lifecycle = _make_lifecycle(store, bus, llm, _FakeEvaluator())
    try:
        t0 = 1_000_000.0
        monkeypatch.setattr("nyx.desire.lifecycle.time.time", lambda: t0)
        # 可表达（0.6 >= 0.5 抑制阈值）但未达峰（< 0.9）→ 释放且不生成
        await store.upsert_value(_dv(DesireType.INTERACTION, 0.6, updated_at=t0))
        d = _desire("d1")
        d.status = DesireStatus.SUPPRESSED
        await store.add_desire(d)
        async with _running(bus):
            await lifecycle.run_eval()
        got = await store.get_desire("d1")
        assert got is not None and got.status is DesireStatus.PENDING
        assert llm.calls == []                                  # 未达峰不生成
    finally:
        await database.conn.close()


async def test_run_eval_keeps_suppressed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, bus, database = await _new_stack()
    llm = _FakeLlm()
    lifecycle = _make_lifecycle(store, bus, llm, _FakeEvaluator())
    try:
        t0 = 1_000_000.0
        monkeypatch.setattr("nyx.desire.lifecycle.time.time", lambda: t0)
        # 不可表达（0.4 < 0.5 抑制阈值）→ 保持 SUPPRESSED
        await store.upsert_value(_dv(DesireType.INTERACTION, 0.4, updated_at=t0))
        d = _desire("d1")
        d.status = DesireStatus.SUPPRESSED
        await store.add_desire(d)
        async with _running(bus):
            await lifecycle.run_eval()
        got = await store.get_desire("d1")
        assert got is not None and got.status is DesireStatus.SUPPRESSED
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

    async def list_memories() -> list[Memory]:
        return []

    lifecycle = DesireLifecycle(
        store,
        bus,
        cast(LlmClient, llm),
        cast(Evaluator, _BoomEvaluator()),
        DesireConfig(),
        list_memories,
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


# ---- run_eval 去重 ----


async def test_run_eval_dedup_discards_similar(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, bus, database = await _new_stack()
    llm = _FakeLlm()
    evaluator = _FakeEvaluator()

    async def embed(_text: str) -> list[float]:
        # 已有「读骑士小说」与新生成「读一段骑士团的历史」同向量 → 余弦 1.0 ≥ 0.9
        return [1.0, 0.0]

    lifecycle = _make_lifecycle(store, bus, llm, evaluator, embed=embed)
    events = _subscribe(bus)
    try:
        t0 = 1_000_000.0
        monkeypatch.setattr("nyx.desire.lifecycle.time.time", lambda: t0)
        await store.upsert_value(_dv(DesireType.INTERACTION, 0.9, updated_at=t0))
        await store.add_desire(_desire("existing"))
        async with _running(bus):
            result = await lifecycle.run_eval()
        assert result == []                                   # 重复 → 丢弃
        assert [d.id for d in await store.list_pending()] == ["existing"]
        assert [e for e in events if e.type is EventType.DESIRE_GENERATED] == []
        dv = await store.get_value(DesireType.INTERACTION)
        assert dv is not None and dv.value == pytest.approx(0.0)   # 目标已重置
    finally:
        await database.conn.close()


async def test_run_eval_dedup_keeps_distinct(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, bus, database = await _new_stack()
    llm = _FakeLlm()
    evaluator = _FakeEvaluator()

    async def embed(text: str) -> list[float]:
        # 两条 description 正交向量 → 余弦 0 < 0.9，不判重复
        return {"读骑士小说": [1.0, 0.0], "读一段骑士团的历史": [0.0, 1.0]}[text]

    lifecycle = _make_lifecycle(store, bus, llm, evaluator, embed=embed)
    events = _subscribe(bus)
    try:
        t0 = 1_000_000.0
        monkeypatch.setattr("nyx.desire.lifecycle.time.time", lambda: t0)
        await store.upsert_value(_dv(DesireType.INTERACTION, 0.9, updated_at=t0))
        await store.add_desire(_desire("existing"))
        async with _running(bus):
            result = await lifecycle.run_eval()
        assert len(result) == 1                               # 不重复 → 入队
        assert [d.id for d in await store.list_pending()] == ["existing", result[0].id]
        assert len([e for e in events if e.type is EventType.DESIRE_GENERATED]) == 1
    finally:
        await database.conn.close()


async def test_run_eval_dedup_disabled_without_embed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, bus, database = await _new_stack()
    llm = _FakeLlm()
    lifecycle = _make_lifecycle(store, bus, llm, _FakeEvaluator())  # embed=None
    try:
        t0 = 1_000_000.0
        monkeypatch.setattr("nyx.desire.lifecycle.time.time", lambda: t0)
        await store.upsert_value(_dv(DesireType.INTERACTION, 0.9, updated_at=t0))
        await store.add_desire(_desire("existing"))
        async with _running(bus):
            result = await lifecycle.run_eval()
        assert len(result) == 1                               # 向量层禁用 → 不去重
    finally:
        await database.conn.close()


async def test_run_eval_dedup_embed_error_skips(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, bus, database = await _new_stack()
    llm = _FakeLlm()

    async def embed(_text: str) -> list[float]:
        raise RuntimeError("embed down")

    lifecycle = _make_lifecycle(store, bus, llm, _FakeEvaluator(), embed=embed)
    try:
        t0 = 1_000_000.0
        monkeypatch.setattr("nyx.desire.lifecycle.time.time", lambda: t0)
        await store.upsert_value(_dv(DesireType.INTERACTION, 0.9, updated_at=t0))
        await store.add_desire(_desire("existing"))
        async with _running(bus):
            result = await lifecycle.run_eval()
        assert len(result) == 1                               # embed 失败降级不去重
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
