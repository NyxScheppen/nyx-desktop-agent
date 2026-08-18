# pyright: reportPrivateUsage=false
import asyncio
import contextlib
import uuid
from collections.abc import AsyncGenerator
from typing import Any

import aiosqlite
import pytest

from nyx import db
from nyx.enums import EventType, Source
from nyx.events.bus import _PERSIST_MAX_ATTEMPTS, EventBus
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


async def test_list_events_stable_order_same_timestamp() -> None:
    bus = await _new_bus()
    try:
        async with _running(bus):
            await bus.publish(_make_event(id="b", timestamp=1.0))
            await bus.publish(_make_event(id="a", timestamp=1.0))

        assert [e.id for e in await bus.list_events()] == ["a", "b"]  # id tiebreaker
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


async def test_remove_sse_sink_is_idempotent() -> None:
    bus = await _new_bus()
    try:
        sink: asyncio.Queue[Event] = asyncio.Queue()
        bus.remove_sse_sink(sink)  # 从未加入：不抛
        bus.add_sse_sink(sink)
        bus.remove_sse_sink(sink)
        bus.remove_sse_sink(sink)  # 二次移除：不抛
    finally:
        await _close(bus)


async def test_broadcast_drops_oldest_when_sink_full() -> None:
    bus = await _new_bus()
    try:
        sink: asyncio.Queue[Event] = asyncio.Queue(maxsize=1)
        bus.add_sse_sink(sink)
        bus._broadcast(_make_event(id="a"))
        bus._broadcast(_make_event(id="b"))
        assert sink.qsize() == 1
        assert sink.get_nowait().id == "b"  # 满时丢最旧（a）保最新（b）
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

        assert bus._queue.qsize() == 1  # 事件放回队首不丢（重排队列 join() 会挂）
    finally:
        await _close(bus)


async def test_persist_failure_requeues_and_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bus = await _new_bus()
    try:
        real_persist = bus._persist
        received: list[Event] = []
        calls = 0

        async def flaky(event: Event) -> None:
            nonlocal calls
            calls += 1
            if calls == 1:
                raise aiosqlite.Error("db down")
            await real_persist(event)

        async def handler(event: Event) -> None:
            received.append(event)

        bus.subscribe(EventType.THINK, handler)
        monkeypatch.setattr(bus, "_persist", flaky)

        event = _make_event()

        # 第一次 run()：_persist 失败，事件放回队首，run() 终止
        task = asyncio.create_task(bus.run())
        await bus.publish(event)
        with pytest.raises(aiosqlite.Error):
            await asyncio.wait_for(task, timeout=1.0)

        # 第二次 run()：重试成功，事件落库 + handler 收到（不丢）
        async with _running(bus):
            pass

        assert calls == 2
        assert received == [event]
        [logged] = await bus.list_events()
        assert logged == event
    finally:
        await _close(bus)


# ---- 毒丸死信 ----

async def test_persist_poison_pill_dead_lettered(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    bus = await _new_bus()
    try:

        async def boom(event: Event) -> None:
            raise aiosqlite.Error("db down")

        monkeypatch.setattr(bus, "_persist", boom)
        event = _make_event()
        await bus.publish(event)

        # 前 _PERSIST_MAX_ATTEMPTS-1 轮：放回队首 + 传播（交监督器重启）
        for _ in range(_PERSIST_MAX_ATTEMPTS - 1):
            task = asyncio.create_task(bus.run())
            with pytest.raises(aiosqlite.Error):
                await asyncio.wait_for(task, timeout=1.0)
            assert bus._queue.qsize() == 1  # 事件仍放回队首

        # 第 _PERSIST_MAX_ATTEMPTS 轮：死信丢弃，run() 不死
        task = asyncio.create_task(bus.run())
        await asyncio.wait_for(bus._queue.join(), timeout=1.0)
        assert bus._queue.qsize() == 0  # 事件被丢弃
        assert not task.done()          # run() 继续阻塞等下一个事件
        assert "死信丢弃" in caplog.text
        assert event.id in caplog.text
    finally:
        await _close(bus)


# ---- _persist 回滚 ----

async def test_persist_rolls_back_on_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bus = await _new_bus()
    try:
        rolled_back = False

        async def bad_commit() -> None:
            raise aiosqlite.Error("disk full")

        async def spy_rollback() -> None:
            nonlocal rolled_back
            rolled_back = True

        monkeypatch.setattr(bus._db.conn, "commit", bad_commit)
        monkeypatch.setattr(bus._db.conn, "rollback", spy_rollback)

        with pytest.raises(aiosqlite.Error):
            await bus._persist(_make_event())
        assert rolled_back  # 失败回滚，不留坏事务给下次重试
    finally:
        await _close(bus)


# ---- 序列化 default=str ----

async def test_persist_serializes_non_json_types() -> None:
    bus = await _new_bus()
    try:
        async with _running(bus):
            await bus.publish(_make_event(content={"id": uuid.uuid4()}))
        [logged] = await bus.list_events()
        assert isinstance(logged.content["id"], str)  # UUID 经 default=str 序列化
    finally:
        await _close(bus)


# ---- put_left 补齐 join 语义 ----

async def test_put_left_resets_join() -> None:
    bus = await _new_bus()
    try:
        bus._queue.put_left(_make_event())
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(bus._queue.join(), timeout=0.05)
    finally:
        await _close(bus)
