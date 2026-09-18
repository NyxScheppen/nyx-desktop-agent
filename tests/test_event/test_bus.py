# pyright: reportPrivateUsage=false
import asyncio
import contextlib
import json
import uuid
from collections.abc import AsyncGenerator
from typing import Any

import pytest

from nyx import db
from nyx.enums import EventType, Source
from nyx.events.bus import (
    EventAdmissionError,
    EventBus,
    EventBusClosedError,
)
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
    return EventBus(await db.connect(":memory:"))


@pytest.mark.parametrize(
    ("types", "limit"),
    [((), 100), ((EventType.BROWSING_MUTTER,), 0), ((EventType.BROWSING_MUTTER,), 101)],
)
async def test_correlation_outputs_reject_invalid_query(
    types: tuple[EventType, ...], limit: int
) -> None:
    bus = await _new_bus()
    try:
        with pytest.raises(ValueError):
            await bus.list_events_for_correlation("page", types, limit)
    finally:
        await _close(bus)


async def test_correlation_outputs_select_latest_then_restore_order() -> None:
    bus = await _new_bus()
    try:
        for key in ("a", "b", "c"):
            await bus.publish(_make_event(id=key, type_=EventType.MUTTER))
        await bus.publish(_make_event(id="other", correlation_id="elsewhere"))
        outputs = await bus.list_events_for_correlation(
            "corr-1", (EventType.MUTTER,), limit=2
        )
        assert [event.id for event in outputs] == ["b", "c"]
    finally:
        await _close(bus)


async def _wait_delivery(
    bus: EventBus,
    event_id: str,
    *,
    status: str,
    consumer_id: str | None = None,
) -> dict[str, Any]:
    for _ in range(200):
        sql = (
            "SELECT event_id, consumer_id, status, attempts, last_error "
            "FROM event_delivery WHERE event_id = ?"
        )
        params: list[str] = [event_id]
        if consumer_id is not None:
            sql += " AND consumer_id = ?"
            params.append(consumer_id)
        async with bus._db.lock:
            cursor = await bus._db.conn.execute(sql, params)
            row = await cursor.fetchone()
        if row is not None and row["status"] == status:
            return dict(row)
        await asyncio.sleep(0.01)
    raise AssertionError(f"delivery 未进入 {status}: {event_id}")


@contextlib.asynccontextmanager
async def _running(bus: EventBus) -> AsyncGenerator[None]:
    task = asyncio.create_task(bus.run())
    await asyncio.sleep(0)
    try:
        yield
    finally:
        await bus.drain(timeout=2.0)
        with contextlib.suppress(asyncio.CancelledError):
            await task


async def _close(bus: EventBus) -> None:
    await bus._db.close()


async def test_publish_is_durable_before_return() -> None:
    bus = await _new_bus()
    try:
        called = False

        async def handler(event: Event) -> None:
            nonlocal called
            called = True

        bus.subscribe(EventType.THINK, handler)
        event = _make_event()
        await bus.publish(event)

        assert not called
        assert await bus.list_events() == [event]
        async with bus._db.lock:
            cursor = await bus._db.conn.execute(
                "SELECT status FROM event_delivery WHERE event_id = ?",
                (event.id,),
            )
            row = await cursor.fetchone()
        assert row is not None and row["status"] == "pending"
    finally:
        await _close(bus)


async def test_run_dispatches_and_broadcasts_after_durable_publish() -> None:
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
            await _wait_delivery(
                bus, event.id, status="succeeded", consumer_id="legacy.think.0"
            )

        assert received == [event]
        assert sink.get_nowait() is event
        assert await bus.list_events() == [event]
    finally:
        await _close(bus)


async def test_multiple_handlers_are_independent_consumers() -> None:
    bus = await _new_bus()
    try:
        order: list[str] = []

        async def first(event: Event) -> None:
            order.append("first")

        async def second(event: Event) -> None:
            order.append("second")

        bus.subscribe(EventType.THINK, first)
        bus.subscribe(EventType.THINK, second)
        event = _make_event()
        async with _running(bus):
            await bus.publish(event)
            await _wait_delivery(
                bus, event.id, status="succeeded", consumer_id="legacy.think.0"
            )
            await _wait_delivery(
                bus, event.id, status="succeeded", consumer_id="legacy.think.1"
            )

        assert set(order) == {"first", "second"}
    finally:
        await _close(bus)


async def test_list_events_filter_sort_and_limit() -> None:
    bus = await _new_bus()
    try:
        await bus.publish(_make_event(id="a", timestamp=1.0))
        await bus.publish(
            _make_event(id="b", timestamp=2.0, type_=EventType.SPEAK)
        )
        await bus.publish(_make_event(id="c", timestamp=3.0, correlation_id="c-2"))

        assert [e.id for e in await bus.list_events()] == ["c", "b", "a"]
        assert [e.id for e in await bus.list_events(limit=2)] == ["c", "b"]
        assert [
            e.id
            for e in await bus.list_events(event_type=EventType.SPEAK)
        ] == ["b"]
        assert [
            e.id
            for e in await bus.list_events(correlation_id="c-2")
        ] == ["c"]
    finally:
        await _close(bus)


async def test_same_timestamp_has_stable_id_tiebreak() -> None:
    bus = await _new_bus()
    try:
        await bus.publish(_make_event(id="b", timestamp=1.0))
        await bus.publish(_make_event(id="a", timestamp=1.0))
        assert [e.id for e in await bus.list_events()] == ["a", "b"]
    finally:
        await _close(bus)


async def test_sse_sink_add_remove_and_backpressure() -> None:
    bus = await _new_bus()
    try:
        sink1: asyncio.Queue[Event] = asyncio.Queue()
        sink2: asyncio.Queue[Event] = asyncio.Queue(maxsize=1)
        bus.add_sse_sink(sink1)
        bus.add_sse_sink(sink2)
        bus.remove_sse_sink(sink1)
        await bus.publish(_make_event(id="a"))
        await bus.publish(_make_event(id="b"))
        assert sink1.empty()
        assert sink2.get_nowait().id == "b"
    finally:
        await _close(bus)


async def test_handler_failure_retries_only_failed_consumer(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    import nyx.events.bus as bus_module

    monkeypatch.setattr(bus_module, "_DELIVERY_RETRY_DELAYS", (0.0,) * 5)
    bus = await _new_bus()
    try:
        calls = {"bad": 0, "good": 0}

        async def bad(event: Event) -> None:
            calls["bad"] += 1
            raise RuntimeError("boom")

        async def good(event: Event) -> None:
            calls["good"] += 1

        bus.subscribe(EventType.THINK, bad)
        bus.subscribe(EventType.THINK, good)
        event = _make_event()
        async with _running(bus):
            await bus.publish(event)
            bad_row = await _wait_delivery(
                bus, event.id, status="dead_letter", consumer_id="legacy.think.0"
            )
            good_row = await _wait_delivery(
                bus, event.id, status="succeeded", consumer_id="legacy.think.1"
            )

        assert bad_row["attempts"] == 5
        assert good_row["attempts"] == 1
        assert calls == {"bad": 5, "good": 1}
        assert "handler 处理失败" in caplog.text
    finally:
        await _close(bus)


async def test_delivery_failure_is_recovered_after_expired_lease() -> None:
    bus = await _new_bus()
    try:
        async def handler(event: Event) -> None:
            return None

        bus.subscribe(EventType.THINK, handler)
        event = _make_event()
        await bus.publish(event)
        async with bus._db.lock:
            await bus._db.conn.execute(
                """UPDATE event_delivery
                SET status = 'processing', lease_until = ?
                WHERE event_id = ? AND consumer_id = 'legacy.think.0'""",
                (0.0, event.id),
            )
            await bus._db.conn.commit()
        await bus.recover_deliveries()
        async with bus._db.lock:
            cursor = await bus._db.conn.execute(
                "SELECT status FROM event_delivery WHERE event_id = ?",
                (event.id,),
            )
            row = await cursor.fetchone()
        assert row is not None and row["status"] == "pending"
    finally:
        await _close(bus)


async def test_effect_marker_skips_duplicate_handler_replay() -> None:
    bus = await _new_bus()
    try:
        calls = 0

        async def handler(event: Event) -> None:
            nonlocal calls
            calls += 1

        bus.subscribe(EventType.THINK, handler)
        event = _make_event()
        await bus.publish(event)
        async with bus._db.lock:
            await bus._db.conn.execute(
                """INSERT INTO event_effect (event_id, consumer_id, applied_at)
                VALUES (?, ?, ?)""",
                (event.id, "legacy.think.0", 1.0),
            )
            await bus._db.conn.commit()

        async with _running(bus):
            row = await _wait_delivery(
                bus, event.id, status="succeeded", consumer_id="legacy.think.0"
            )

        assert calls == 0
        assert row["attempts"] == 1
    finally:
        await _close(bus)


async def test_success_finalization_retries_without_blocking_consumer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bus = await _new_bus()
    try:
        received: list[str] = []
        entered = asyncio.Event()
        release = asyncio.Event()

        async def handler(event: Event) -> None:
            received.append(event.id)
            if event.id == "first":
                entered.set()
                await release.wait()

        bus.subscribe(EventType.THINK, handler)
        first = _make_event(id="first", timestamp=1.0)
        second = _make_event(id="second", timestamp=2.0)
        async with _running(bus):
            await bus.publish(first)
            await bus.publish(second)
            await asyncio.wait_for(entered.wait(), timeout=1.0)

            real_commit = bus._db.conn.commit
            failures = 0

            async def fail_once() -> None:
                nonlocal failures
                failures += 1
                if failures == 1:
                    raise RuntimeError("transient commit failure")
                await real_commit()

            monkeypatch.setattr(bus._db.conn, "commit", fail_once)
            release.set()
            await _wait_delivery(bus, first.id, status="succeeded")
            await _wait_delivery(bus, second.id, status="succeeded")

        assert received == ["first", "second"]
    finally:
        await _close(bus)


async def test_failure_finalization_retries_without_wedging_delivery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import nyx.events.bus as bus_module

    monkeypatch.setattr(bus_module, "_DELIVERY_RETRY_DELAYS", (0.0,) * 5)
    bus = await _new_bus()
    try:
        entered = asyncio.Event()
        release = asyncio.Event()
        calls = 0

        async def handler(event: Event) -> None:
            nonlocal calls
            calls += 1
            if calls == 1:
                entered.set()
                await release.wait()
            raise RuntimeError("handler failed")

        bus.subscribe(EventType.THINK, handler)
        event = _make_event()
        async with _running(bus):
            await bus.publish(event)
            await asyncio.wait_for(entered.wait(), timeout=1.0)
            real_commit = bus._db.conn.commit
            failures = 0

            async def fail_once() -> None:
                nonlocal failures
                failures += 1
                if failures == 1:
                    raise RuntimeError("transient commit failure")
                await real_commit()

            monkeypatch.setattr(bus._db.conn, "commit", fail_once)
            release.set()
            row = await _wait_delivery(bus, event.id, status="dead_letter")

        assert row["attempts"] == 5
        assert calls == 5
    finally:
        await _close(bus)


async def test_publish_failure_does_not_return_event_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bus = await _new_bus()
    try:
        async def bad_commit() -> None:
            raise RuntimeError("disk full")

        monkeypatch.setattr(bus._db.conn, "commit", bad_commit)
        with pytest.raises(EventAdmissionError):
            await bus.publish(_make_event())
        assert await bus.list_events() == []
    finally:
        await _close(bus)


async def test_close_rejects_new_events_and_closes_database() -> None:
    bus = await _new_bus()
    await bus.close()
    assert bus._db.is_closed
    with pytest.raises(EventBusClosedError):
        await bus.publish(_make_event())
    await bus.close()


async def test_event_payload_serialization_is_stable() -> None:
    bus = await _new_bus()
    try:
        event = _make_event(content={"id": uuid.uuid4()})
        await bus.publish(event)
        async with bus._db.lock:
            cursor = await bus._db.conn.execute(
                "SELECT content FROM event_log WHERE id = ?", (event.id,)
            )
            row = await cursor.fetchone()
        assert row is not None
        assert json.loads(row["content"])["id"] == str(event.content["id"])
    finally:
        await _close(bus)
