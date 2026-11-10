# pyright: reportPrivateUsage=false
import asyncio
import contextlib
import io
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest

from nyx import main as entry
from nyx.activity.facade import ActivityFacade
from nyx.config import Config
from nyx.desire.facade import DesireFacade
from nyx.enums import EventType, MemoryKind, MemoryType, Source
from nyx.eval.evaluator import Evaluator
from nyx.eval.store import EvalStore
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
from nyx.reading.facade import ReadingFacade
from nyx.runtime import check_initiate_chat, memory_loop
from nyx.types import Event, InnerDocument, InnerTopic, Memory


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


async def test_waiting_question_blocks_desire_initiate_chat() -> None:
    expression = SimpleNamespace(
        pending_questions=AsyncMock(return_value=[object()]),
        initiate_chat=AsyncMock(),
    )
    desire = SimpleNamespace(get_pending=AsyncMock())
    app = SimpleNamespace(expression=expression, desire=desire)

    await check_initiate_chat(app)

    desire.get_pending.assert_not_awaited()
    expression.initiate_chat.assert_not_awaited()


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
        reading=cast(ReadingFacade, object()),
        evaluator=cast(Evaluator, object()),
        eval_store=cast(EvalStore, object()),
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
        "schedule_block_start",
        "desire_eval",
        "mutter_check",
        "initiate_chat_check",
    }
    assert all(e.source is Source.INTERNAL for e in bus.published)


async def test_memory_loop_uses_its_own_timer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    memory = SimpleNamespace(maintain_conversation_segments=AsyncMock())
    app = SimpleNamespace(memory=memory)
    async def tick(_interval: float) -> None:
        if memory.maintain_conversation_segments.await_count:
            raise _StopLoop()

    monkeypatch.setattr("nyx.runtime.asyncio.sleep", tick)

    with pytest.raises(_StopLoop):
        await memory_loop(app, interval=60.0)

    memory.maintain_conversation_segments.assert_awaited_once()


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


class _NormallyStoppedBus:
    def __init__(self) -> None:
        self.calls = 0
        self.persisted_count = 0

    async def run(self) -> None:
        self.calls += 1


async def test_supervise_bus_returns_when_bus_stops_normally() -> None:
    bus = _NormallyStoppedBus()

    await asyncio.wait_for(_supervise_bus(_app(bus)), timeout=0.1)

    assert bus.calls == 1


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


def _fake_host() -> MagicMock:
    host = MagicMock(records={}, apps={}, stopping={}, presence_tasks={})
    host.close = AsyncMock()
    return host


def _fake_uvicorn_config(*args: object, **kwargs: object) -> object:
    return object()


async def test_main_propagates_serve_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    server = MagicMock()
    server.serve = AsyncMock(side_effect=RuntimeError("port in use"))
    host = _fake_host()
    monkeypatch.setattr(entry, "load_config", lambda: Config())
    monkeypatch.setattr(entry._Host, "open", AsyncMock(return_value=host))
    monkeypatch.setattr(entry.uvicorn, "Config", _fake_uvicorn_config)
    monkeypatch.setattr(entry.uvicorn, "Server", MagicMock(return_value=server))
    with pytest.raises(RuntimeError, match="port in use"):
        await main()
    host.close.assert_awaited_once()


async def test_main_activates_only_running_roles(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    host = _fake_host()
    host.records = {"nyx": {"status": "RUNNING"}, "alice": {"status": "FAILED"}}
    server = MagicMock()
    server.serve = AsyncMock(side_effect=RuntimeError("stop"))
    monkeypatch.setattr(entry, "load_config", lambda: Config())
    monkeypatch.setattr(entry._Host, "open", AsyncMock(return_value=host))
    monkeypatch.setattr(entry.uvicorn, "Config", _fake_uvicorn_config)
    monkeypatch.setattr(entry.uvicorn, "Server", MagicMock(return_value=server))
    with pytest.raises(RuntimeError, match="stop"):
        await main()
    host.activate.assert_called_once_with("nyx")
    host.close.assert_awaited_once()


@pytest.mark.parametrize(
    ("configured_port", "expected_host", "expected_port"),
    [(None, "127.0.0.1", 8000), ("9090", "0.0.0.0", 9090)],
)
async def test_main_uses_platform_port_when_configured(
    monkeypatch: pytest.MonkeyPatch,
    configured_port: str | None,
    expected_host: str,
    expected_port: int,
) -> None:
    server = MagicMock()
    server.serve = AsyncMock(side_effect=RuntimeError("stop"))
    host = _fake_host()
    config_args: dict[str, object] = {}

    def capture_config(*args: object, **kwargs: object) -> object:
        config_args.update(kwargs)
        return object()

    if configured_port is None:
        monkeypatch.delenv("PORT", raising=False)
    else:
        monkeypatch.setenv("PORT", configured_port)
    monkeypatch.setattr(entry, "load_config", lambda: Config())
    monkeypatch.setattr(entry._Host, "open", AsyncMock(return_value=host))
    monkeypatch.setattr(entry.uvicorn, "Config", capture_config)
    monkeypatch.setattr(entry.uvicorn, "Server", MagicMock(return_value=server))
    with pytest.raises(RuntimeError, match="stop"):
        await main()
    assert config_args["host"] == expected_host
    assert config_args["port"] == expected_port


@pytest.mark.parametrize("value", ["0", "65536", "not-a-port"])
def test_server_bind_rejects_invalid_platform_port(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv("PORT", value)
    with pytest.raises(ValueError, match="PORT"):
        entry._server_bind()


async def test_main_rejects_invalid_platform_port_before_opening_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PORT", "not-a-port")
    monkeypatch.setattr(entry, "load_config", lambda: Config())
    open_host = AsyncMock()
    monkeypatch.setattr(entry._Host, "open", open_host)
    with pytest.raises(ValueError, match="PORT"):
        await main()
    open_host.assert_not_awaited()


async def test_frozen_parent_pipe_eof_requests_exit_without_blocking_shutdown(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def frozen_config(_path: str) -> Config:
        return Config()

    server = MagicMock(should_exit=False)
    server.serve = AsyncMock(side_effect=RuntimeError("serve failed"))

    def frozen_server(_config: object) -> MagicMock:
        return server

    thread = MagicMock()
    monkeypatch.setattr(entry.sys, "frozen", True, raising=False)
    monkeypatch.setattr(entry.sys, "_MEIPASS", str(tmp_path), raising=False)
    monkeypatch.setattr(entry.sys, "stdin", MagicMock(buffer=io.BytesIO()))
    monkeypatch.setenv("NYX_CANON_DIR", str(tmp_path))
    monkeypatch.setattr(entry, "load_config", frozen_config)
    monkeypatch.setattr(entry._Host, "open", AsyncMock(return_value=_fake_host()))
    monkeypatch.setattr(entry.uvicorn, "Config", _fake_uvicorn_config)
    monkeypatch.setattr(entry.uvicorn, "Server", frozen_server)
    with pytest.MonkeyPatch.context() as watcher:
        watcher.setattr(entry.threading, "Thread", thread)
        with pytest.raises(RuntimeError, match="serve failed"):
            await asyncio.wait_for(main(), timeout=1)
    assert thread.call_args.kwargs["daemon"] is True
    thread.call_args.kwargs["target"]()
    assert server.should_exit is True


async def test_first_tick_starts_activity_not_mutter_or_chat(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bus = _FakeBus()
    app = _app(bus, grid_minutes=60)
    monkeypatch.setattr("nyx.main.asyncio.sleep", _stop)

    with pytest.raises(_StopLoop):
        await _tick_loop(app)

    assert [e.content["tick_type"] for e in bus.published] == [
        "schedule_block_start",
        "desire_eval",
    ]


# ---- _check_reflect：反思检查三分支 ----


class _FakeInnerLife:
    def __init__(self, narrative: InnerDocument) -> None:
        self._narrative = narrative

    async def get_document(self) -> InnerDocument:
        return self._narrative


class _FakeMemory:
    def __init__(
        self, memories: list[Memory], count_new_result: int | None = None
    ) -> None:
        self._memories = memories
        self._count_new_result = count_new_result

    async def list_memories(self) -> list[Memory]:
        return self._memories

    async def count_new(self, kind: MemoryKind | None, since: float) -> int:
        del kind
        if self._count_new_result is not None:
            return self._count_new_result
        return sum(1 for memory in self._memories if memory.created_at > since)


def _memory(created_at: float) -> Memory:
    return Memory(
        id=f"m{created_at}",
        created_at=created_at,
        content="c",
        kind=MemoryKind.USER_PROFILE,
        summary="s",
        freshness=1.0,
        type=MemoryType.SHORT_TERM,
    )


def _reflect_app(
    narrative: InnerDocument,
    memories: list[Memory],
    count_new_result: int | None = None,
) -> tuple[_App, _FakeInnerLife, _FakeBus]:
    inner_life = _FakeInnerLife(narrative)
    memory = _FakeMemory(memories, count_new_result)
    bus = _FakeBus()
    app = _App(
        bus=cast(EventBus, bus),
        inner_life=cast(InnerLifeFacade, inner_life),
        desire=cast(DesireFacade, object()),
        memory=cast(MemoryFacade, memory),
        activity=cast(ActivityFacade, object()),
        expression=cast(ExpressionFacade, _FakeExpression()),
        reading=cast(ReadingFacade, object()),
        evaluator=cast(Evaluator, object()),
        eval_store=cast(EvalStore, object()),
        config=Config(),
    )
    return app, inner_life, bus


async def test_check_reflect_skips_within_cooldown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = 1_000_000.0
    monkeypatch.setattr("nyx.main.time.time", lambda: now)
    narrative = InnerDocument(
        topics=[
            InnerTopic("identity", "self-understanding", "自我理解", "尼克斯"),
        ],
        revision=1,
        updated_at=now - 100.0,
    )
    memories = [_memory(now - 50.0) for _ in range(_REFLECT_MIN_NEW_MEMORIES)]
    app, _inner_life, bus = _reflect_app(narrative, memories)
    await _check_reflect(app, "cid")
    assert bus.published == []


async def test_check_reflect_skips_below_new_memory_threshold(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = 1_000_000.0
    monkeypatch.setattr("nyx.main.time.time", lambda: now)
    narrative = InnerDocument(
        topics=[
            InnerTopic("identity", "self-understanding", "自我理解", "尼克斯"),
        ],
        revision=1,
        updated_at=now - _REFLECT_MIN_INTERVAL - 1000.0,
    )
    memories = [_memory(narrative.updated_at + 100.0) for _ in range(2)]
    app, _inner_life, bus = _reflect_app(narrative, memories)
    await _check_reflect(app, "cid")
    assert bus.published == []


async def test_check_reflect_triggers(monkeypatch: pytest.MonkeyPatch) -> None:
    now = 1_000_000.0
    monkeypatch.setattr("nyx.main.time.time", lambda: now)
    narrative = InnerDocument(
        topics=[
            InnerTopic("identity", "self-understanding", "自我理解", "尼克斯"),
        ],
        revision=1,
        updated_at=now - _REFLECT_MIN_INTERVAL - 1000.0,
    )
    memories = [
        _memory(narrative.updated_at + 100.0) for _ in range(_REFLECT_MIN_NEW_MEMORIES)
    ]
    app, _inner_life, bus = _reflect_app(narrative, memories)
    await _check_reflect(app, "cid")
    assert [event.type for event in bus.published] == [EventType.REFLECTION]
    assert bus.published[0].correlation_id == "cid"
    assert bus.published[0].content == {
        "reason": "periodic",
        "narrative_updated_at": narrative.updated_at,
        "min_interval": _REFLECT_MIN_INTERVAL,
        "min_new_memories": _REFLECT_MIN_NEW_MEMORIES,
        "new_memory_count": _REFLECT_MIN_NEW_MEMORIES,
        "evidence": "距上次反思已满 21600 秒，期间新增 3 条记忆",
    }


async def test_check_reflect_uses_first_creation_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = 1_000_000.0
    monkeypatch.setattr("nyx.main.time.time", lambda: now)
    narrative = InnerDocument(
        topics=[
            InnerTopic("identity", "self-understanding", "自我理解", "尼克斯"),
        ],
        revision=1,
        updated_at=now - _REFLECT_MIN_INTERVAL - 1000.0,
    )
    memories = [
        _memory(narrative.updated_at + 100.0) for _ in range(_REFLECT_MIN_NEW_MEMORIES)
    ]
    app, _inner_life, bus = _reflect_app(narrative, memories, count_new_result=0)

    await _check_reflect(app, "cid")

    assert bus.published == []
