# pyright: reportPrivateUsage=false
import asyncio
import contextlib
from collections.abc import AsyncGenerator
from typing import Any

import aiosqlite
import pytest

from nyx import db
from nyx.enums import EventType, Source
from nyx.events.bus import EventBus
from nyx.types import Event


def _make_event(
    *,
    id: str = "evt-1",
    timestamp: float = 1000.0,
    type_: EventType = EventType.THINK,
    correlation_id: str = "corr-1",
    content: dict[str, Any] | None = None,
) -> Event:
    return Event(
        id=id,
        timestamp=timestamp,
        source=Source.INTERNAL,
        type=type_,
        content=content if content is not None else {"text": "hi"},
        correlation_id=correlation_id,
    )


async def _new_bus() -> EventBus:
    database = await db.connect(":memory:")
    return EventBus(database)


async def _close(bus: EventBus) -> None:
    await bus._db.conn.close()


@contextlib.asynccontextmanager
async def _running(bus: EventBus) -> AsyncGenerator[None]:
    """以 task 跑 run()，yield 后等待队列排空，退出时 cancel。"""
    task = asyncio.create_task(bus.run())
    try:
        yield
        await asyncio.wait_for(bus._queue.join(), timeout=1.0)
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


# ---- publish 只入队 ----

async def test_publish_only_enqueues() -> None:
    bus = await _new_bus()
    try:
        called = False

        async def handler(event: Event) -> None:
            nonlocal called
            called = True

        bus.subscribe(EventType.THINK, handler)
        await bus.publish(_make_event())
        assert not called
        assert await bus.list_events() == []
    finally:
        await _close(bus)


# ---- run：persist → 分发 → 广播 ----

async def test_run_persists_dispatches_and_broadcasts() -> None:
    bus = await _new_bus()
    try:
        received: list[Event] = []
        sink: asyncio.Queue[Event] = asyncio.Queue()

        async def handler(event: Event) -> None:
            received.append(event)

        bus.subscribe(EventType.THINK, handler)
        bus.add_sse_sink(sink)

        event = _make_event(content={"text": "hello", "n": 1})
        async with _running(bus):
            await bus.publish(event)

        assert received == [event]  # handler 收到完整 Event
        assert sink.get_nowait() is event  # SSE 收到同一 Event
        [logged] = await bus.list_events()
        assert logged == event  # 落库往返（含 correlation_id 透传）
    finally:
        await _close(bus)


# ---- 多 handler 订阅序 ----

async def test_multiple_handlers_run_in_subscribe_order() -> None:
    bus = await _new_bus()
    try:
        order: list[str] = []

        async def first(event: Event) -> None:
            order.append("first")

        async def second(event: Event) -> None:
            order.append("second")

        bus.subscribe(EventType.THINK, first)
        bus.subscribe(EventType.THINK, second)

        async with _running(bus):
            await bus.publish(_make_event())

        assert order == ["first", "second"]
    finally:
        await _close(bus)


# ---- list_events 过滤 / 排序 ----

async def test_list_events_filter_by_type() -> None:
    bus = await _new_bus()
    try:
        async with _running(bus):
            await bus.publish(_make_event(id="a", type_=EventType.THINK))
            await bus.publish(_make_event(id="b", type_=EventType.SPEAK))

        by_type = await bus.list_events(event_type=EventType.THINK)
        assert [e.id for e in by_type] == ["a"]
    finally:
        await _close(bus)


async def test_list_events_filter_by_correlation() -> None:
    bus = await _new_bus()
    try:
        async with _running(bus):
            await bus.publish(_make_event(id="a", correlation_id="c-1"))
            await bus.publish(_make_event(id="b", correlation_id="c-2"))

        by_corr = await bus.list_events(correlation_id="c-1")
        assert [e.id for e in by_corr] == ["a"]
    finally:
        await _close(bus)


async def test_list_events_sorts_desc_and_limits() -> None:
    bus = await _new_bus()
    try:
        async with _running(bus):
            await bus.publish(_make_event(id="a", timestamp=1.0))
            await bus.publish(_make_event(id="b", timestamp=2.0))
            await bus.publish(_make_event(id="c", timestamp=3.0))

        assert [e.id for e in await bus.list_events()] == ["c", "b", "a"]
        assert [e.id for e in await bus.list_events(limit=2)] == ["c", "b"]
    finally:
        await _close(bus)


# ---- 行↔Event 往返 ----

async def test_row_to_event_roundtrip() -> None:
    bus = await _new_bus()
    try:
        content = {"text": "x", "nested": {"a": [1, 2]}}
        async with _running(bus):
            await bus.publish(_make_event(content=content))

        [row] = await bus.list_events()
        assert row.content == content  # JSON dict 往返
        assert row.source is Source.INTERNAL  # .value 转回枚举
        assert row.type is EventType.THINK
    finally:
        await _close(bus)


# ---- SSE sink 增删 ----

async def test_add_and_remove_sse_sink() -> None:
    bus = await _new_bus()
    try:
        sink1: asyncio.Queue[Event] = asyncio.Queue()
        sink2: asyncio.Queue[Event] = asyncio.Queue()
        bus.add_sse_sink(sink1)
        bus.add_sse_sink(sink2)
        bus.remove_sse_sink(sink1)

        async with _running(bus):
            await bus.publish(_make_event())

        assert sink1.empty()  # 移除后不再收到
        assert sink2.get_nowait() is not None
    finally:
        await _close(bus)


# ---- handler 异常隔离 ----

async def test_handler_exception_isolated(
    caplog: pytest.LogCaptureFixture,
) -> None:
    bus = await _new_bus()
    try:
        order: list[str] = []
        sink: asyncio.Queue[Event] = asyncio.Queue()

        async def bad(event: Event) -> None:
            order.append("bad")
            raise RuntimeError("boom")

        async def good(event: Event) -> None:
            order.append("good")

        bus.subscribe(EventType.THINK, bad)
        bus.subscribe(EventType.THINK, good)
        bus.add_sse_sink(sink)

        task = asyncio.create_task(bus.run())
        try:
            event = _make_event()
            await bus.publish(event)
            await asyncio.wait_for(bus._queue.join(), timeout=1.0)
            assert not task.done()  # run 任务不死
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

        assert order == ["bad", "good"]  # 后续 handler 照跑
        assert sink.get_nowait() is event  # SSE 照广播
        assert "handler 处理事件失败" in caplog.text
        assert "RuntimeError" in caplog.text  # 完整 traceback 被记录
    finally:
        await _close(bus)


# ---- _persist 异常传播 ----

async def test_persist_exception_propagates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bus = await _new_bus()
    try:

        async def boom(event: Event) -> None:
            raise aiosqlite.Error("db down")

        monkeypatch.setattr(bus, "_persist", boom)

        task = asyncio.create_task(bus.run())
        await bus.publish(_make_event())
        with pytest.raises(aiosqlite.Error):
            await asyncio.wait_for(task, timeout=1.0)

        await asyncio.wait_for(bus._queue.join(), timeout=1.0)  # task_done 照走
    finally:
        await _close(bus)
