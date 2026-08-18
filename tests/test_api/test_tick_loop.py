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
from nyx.main import _App, _supervise_bus, _tick_loop
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


def _app(bus: object) -> _App:
    config = Config()
    config.activity.grid_minutes = 0
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
    """run() 每轮都 raise，供 _supervise_bus 重启测试。"""

    def __init__(self) -> None:
        self.calls = 0

    async def run(self) -> None:
        self.calls += 1
        raise RuntimeError("db down")


async def test_supervise_bus_restarts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("nyx.main._BUS_RESTART_DELAY", 0.0)
    bus = _FlakyBus()
    app = _app(bus)
    task = asyncio.create_task(_supervise_bus(app))
    try:
        for _ in range(100):
            await asyncio.sleep(0)
            if bus.calls >= 2:
                break
        assert bus.calls >= 2  # 异常后被重启，run() 至少被调两次
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
