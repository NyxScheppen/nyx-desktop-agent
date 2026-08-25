# pyright: reportPrivateUsage=false
import asyncio
import contextlib
from typing import cast

import pytest

from nyx.activity.facade import ActivityFacade
from nyx.config import Config
from nyx.desire.facade import DesireFacade
from nyx.encounter.facade import EncounterFacade
from nyx.enums import EventType, MemoryType, Source
from nyx.eval.evaluator import Evaluator
from nyx.events.bus import EventBus
from nyx.expression.facade import ExpressionFacade
from nyx.inner_life.facade import InnerLifeFacade
from nyx.main import (
    _BUS_MAX_FAILURES,
    _BUS_RECOVERY_STREAK,
    _REFLECT_MIN_INTERVAL,
    _REFLECT_MIN_NEW_MEMORIES,
    _App,
    _check_reflect,
    _supervise_bus,
    _tick_loop,
    main,
)
from nyx.memory.facade import MemoryFacade
from nyx.types import Event, Memory, SelfNarrative


class _FakeBus:
    def __init__(self) -> None:
        self.published: list[Event] = []

    async def publish(self, event: Event) -> None:
        self.published.append(event)


class _StopLoop(Exception):
    pass


class _FakeExpression:
    """expression 占位：tick 循环直呼 check_timeouts，无操作即可。"""

    async def check_timeouts(self, now: float) -> None:
        return None


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
        expression=cast(ExpressionFacade, _FakeExpression()),
        encounter=cast(EncounterFacade, object()),
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
    """run() 每轮落库 _BUS_RECOVERY_STREAK 次后 raise：达恢复阈值，永不假熔断。"""

    def __init__(self) -> None:
        self.calls = 0
        self.persisted_count = 0

    async def run(self) -> None:
        self.calls += 1
        self.persisted_count += _BUS_RECOVERY_STREAK
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
        assert not task.done()  # 达恢复阈值 → 计数重置，永不假熔断
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


class _FlappingBus:
    """run() 每次成功落库一次后 raise：单次成功不足恢复阈值 → 抖动也熔断。"""

    def __init__(self) -> None:
        self.calls = 0
        self.persisted_count = 0

    async def run(self) -> None:
        self.calls += 1
        self.persisted_count += 1
        raise RuntimeError("flap")


async def test_supervise_bus_breaks_on_flapping(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("nyx.main._BUS_BACKOFF_BASE", 0.0)
    monkeypatch.setattr("nyx.main._BUS_BACKOFF_MAX", 0.0)
    bus = _FlappingBus()
    app = _app(bus)
    with pytest.raises(RuntimeError):
        await _supervise_bus(app)
    assert bus.calls == _BUS_MAX_FAILURES  # 单次成功不足阈值 → 计数不重置 → 熔断


# ---- main() 竞速：任一先完成者异常传播 ----

class _BlockingBus:
    """run()/serve 永阻塞（供 main() 竞速测试），publish 无操作。"""

    def __init__(self) -> None:
        self.persisted_count = 0
        self.published: list[Event] = []

    async def run(self) -> None:
        await asyncio.Event().wait()  # 永不返回

    async def publish(self, event: Event) -> None:
        self.published.append(event)


async def _fake_context(config: Config) -> _App:
    return _app(_BlockingBus())


class _BlockServer:
    """serve() 永阻塞，供 main() 竞速测试（非 serve 先完成）。"""

    def __init__(self, config: object) -> None:
        pass

    async def serve(self) -> None:
        await asyncio.Event().wait()  # 永不返回


def _fake_build_app(app: _App) -> object:
    return object()


def _fake_uvicorn_config(*args: object, **kwargs: object) -> object:
    return object()


async def test_main_propagates_serve_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _FailServer:
        def __init__(self, config: object) -> None:
            pass

        async def serve(self) -> None:
            # 用 RuntimeError 而非 SystemExit：后者是 BaseException，asyncio 会经
            # Handle._run 直接重抛出事件循环，绕开 main() 的 task.result() 重抛路径
            # 无法被 pytest.raises 干净断言。
            raise RuntimeError("port in use")

    monkeypatch.setattr("nyx.main.load_config", lambda: Config())
    monkeypatch.setattr("nyx.main.build_app_context", _fake_context)
    monkeypatch.setattr("nyx.main.build_app", _fake_build_app)
    monkeypatch.setattr("nyx.main.uvicorn.Config", _fake_uvicorn_config)
    monkeypatch.setattr("nyx.main.uvicorn.Server", _FailServer)

    with pytest.raises(RuntimeError):
        await main()


async def test_main_propagates_tick_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def boom_tick(_app: _App) -> None:
        raise RuntimeError("tick broken")

    monkeypatch.setattr("nyx.main.load_config", lambda: Config())
    monkeypatch.setattr("nyx.main.build_app_context", _fake_context)
    monkeypatch.setattr("nyx.main.build_app", _fake_build_app)
    monkeypatch.setattr("nyx.main.uvicorn.Config", _fake_uvicorn_config)
    monkeypatch.setattr("nyx.main.uvicorn.Server", _BlockServer)
    monkeypatch.setattr("nyx.main._tick_loop", boom_tick)

    with pytest.raises(RuntimeError):
        await main()


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


# ---- _check_reflect：反思检查三分支 ----

class _FakeInnerLife:
    def __init__(self, narrative: SelfNarrative) -> None:
        self._narrative = narrative
        self.reflect_calls: list[str] = []

    async def get_narrative(self) -> SelfNarrative:
        return self._narrative

    async def reflect(self, correlation_id: str) -> None:
        self.reflect_calls.append(correlation_id)


class _FakeMemory:
    def __init__(self, memories: list[Memory]) -> None:
        self._memories = memories

    async def list_memories(self) -> list[Memory]:
        return self._memories


def _memory(created_at: float) -> Memory:
    return Memory(
        id=f"m{created_at}", created_at=created_at, content="c", tag="user",
        summary="s", freshness=1.0, type=MemoryType.SHORT_TERM,
    )


def _reflect_app(
    narrative: SelfNarrative, memories: list[Memory]
) -> tuple[_App, _FakeInnerLife]:
    inner_life = _FakeInnerLife(narrative)
    memory = _FakeMemory(memories)
    app = _App(
        bus=cast(EventBus, object()),
        inner_life=cast(InnerLifeFacade, inner_life),
        desire=cast(DesireFacade, object()),
        memory=cast(MemoryFacade, memory),
        activity=cast(ActivityFacade, object()),
        expression=cast(ExpressionFacade, _FakeExpression()),
        encounter=cast(EncounterFacade, object()),
        evaluator=cast(Evaluator, object()),
        config=Config(),
    )
    return app, inner_life


async def test_check_reflect_skips_within_cooldown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = 1_000_000.0
    monkeypatch.setattr("nyx.main.time.time", lambda: now)
    narrative = SelfNarrative(
        identity="尼克斯", story=[], self_view={}, becoming=[],
        updated_at=now - 100.0,  # 距上次反思仅 100s < 冷却
    )
    memories = [_memory(now - 50.0) for _ in range(_REFLECT_MIN_NEW_MEMORIES)]
    app, inner_life = _reflect_app(narrative, memories)
    await _check_reflect(app, "cid")
    assert inner_life.reflect_calls == []


async def test_check_reflect_skips_below_new_memory_threshold(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = 1_000_000.0
    monkeypatch.setattr("nyx.main.time.time", lambda: now)
    narrative = SelfNarrative(
        identity="尼克斯", story=[], self_view={}, becoming=[],
        updated_at=now - _REFLECT_MIN_INTERVAL - 1000.0,  # 已过冷却
    )
    memories = [_memory(narrative.updated_at + 100.0) for _ in range(2)]
    app, inner_life = _reflect_app(narrative, memories)
    await _check_reflect(app, "cid")
    assert inner_life.reflect_calls == []


async def test_check_reflect_triggers(monkeypatch: pytest.MonkeyPatch) -> None:
    now = 1_000_000.0
    monkeypatch.setattr("nyx.main.time.time", lambda: now)
    narrative = SelfNarrative(
        identity="尼克斯", story=[], self_view={}, becoming=[],
        updated_at=now - _REFLECT_MIN_INTERVAL - 1000.0,  # 已过冷却
    )
    memories = [
        _memory(narrative.updated_at + 100.0)
        for _ in range(_REFLECT_MIN_NEW_MEMORIES)
    ]
    app, inner_life = _reflect_app(narrative, memories)
    await _check_reflect(app, "cid")
    assert inner_life.reflect_calls == ["cid"]
