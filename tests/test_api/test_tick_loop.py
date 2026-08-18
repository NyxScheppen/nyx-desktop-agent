# pyright: reportPrivateUsage=false
import asyncio
import contextlib
from typing import cast

import pytest

from nyx.activity.facade import ActivityFacade
from nyx.config import Config
from nyx.desire.facade import DesireFacade
from nyx.enums import EventType, Source
from nyx.eval.evaluator import Evaluator
from nyx.events.bus import EventBus
from nyx.expression.facade import ExpressionFacade
from nyx.inner_life.facade import InnerLifeFacade
from nyx.main import _BUS_MAX_FAILURES, _App, _supervise_bus, _tick_loop
from nyx.memory.facade import MemoryFacade
from nyx.types import Event


class _FakeBus:
    def __init__(self) -> None:
        self.published: list[Event] = []

    async def publish(self, event: Event) -> None:
        self.published.append(event)


class _StopLoop(Exception):
    pass


async def _stop(*_args: object) -> None:
    raise _StopLoop()


def _app(bus: object, *, grid_minutes: int = 0) -> _App:
    config = Config()
    config.activity.grid_minutes = grid_minutes
    return _App(
        bus=cast(EventBus, bus),
        inner_life=cast(InnerLifeFacade, object()),
        desire=cast(DesireFacade, object()),
        memory=cast(MemoryFacade, object()),
        activity=cast(ActivityFacade, object()),
        expression=cast(ExpressionFacade, object()),
        evaluator=cast(Evaluator, object()),
        config=config,
    )


async def test_tick_loop_emits_four_clock_ticks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bus = _FakeBus()
    app = _app(bus)
    monkeypatch.setattr("nyx.main._MUTTER_CHECK_INTERVAL", 0.0)
    monkeypatch.setattr("nyx.main._INITIATE_CHAT_INTERVAL", 0.0)
    monkeypatch.setattr("nyx.main.asyncio.sleep", _stop)

    with pytest.raises(_StopLoop):
        await _tick_loop(app)

    assert [e.type for e in bus.published] == [EventType.CLOCK_TICK] * 4
    assert {e.content["tick_type"] for e in bus.published} == {
        "schedule_block_start", "desire_eval", "mutter_check",
        "initiate_chat_check",
    }
    assert all(e.source is Source.INTERNAL for e in bus.published)


class _FlakyBus:
    """run() 每轮都 raise（无成功落库），供 _supervise_bus 熔断测试。"""

    def __init__(self) -> None:
        self.calls = 0
        self.persisted_count = 0

    async def run(self) -> None:
        self.calls += 1
        raise RuntimeError("db down")


async def test_supervise_bus_breaks_after_max_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("nyx.main._BUS_BACKOFF_BASE", 0.0)
    monkeypatch.setattr("nyx.main._BUS_BACKOFF_MAX", 0.0)
    bus = _FlakyBus()
    app = _app(bus)
    with pytest.raises(RuntimeError):
        await _supervise_bus(app)
    assert bus.calls == _BUS_MAX_FAILURES  # 连续失败到阈值 → 熔断重抛


class _RecoveringBus:
    """run() 每次成功落库一次后 raise：崩溃前有成功落库 → 恢复信号，永不假熔断。"""

    def __init__(self) -> None:
        self.calls = 0
        self.persisted_count = 0

    async def run(self) -> None:
        self.calls += 1
        self.persisted_count += 1
        raise RuntimeError("transient")


async def test_supervise_bus_resets_on_recovery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("nyx.main._BUS_BACKOFF_BASE", 0.0)
    monkeypatch.setattr("nyx.main._BUS_BACKOFF_MAX", 0.0)
    bus = _RecoveringBus()
    app = _app(bus)
    task = asyncio.create_task(_supervise_bus(app))
    try:
        for _ in range(100):
            await asyncio.sleep(0)
            if bus.calls > _BUS_MAX_FAILURES:
                break
        assert bus.calls > _BUS_MAX_FAILURES  # 超过阈值仍未熔断
        assert not task.done()  # 恢复信号 → 计数重置，永不假熔断
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


async def test_first_tick_starts_activity_not_mutter_or_chat(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bus = _FakeBus()
    app = _app(bus, grid_minutes=60)
    monkeypatch.setattr("nyx.main.asyncio.sleep", _stop)

    with pytest.raises(_StopLoop):
        await _tick_loop(app)

    assert [e.content["tick_type"] for e in bus.published] == [
        "schedule_block_start", "desire_eval",
    ]
