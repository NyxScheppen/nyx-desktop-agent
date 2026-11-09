# pyright: reportPrivateUsage=false
import asyncio
import json
import time
from typing import Any

import pytest
from websockets.asyncio.server import ServerConnection, serve

from nyx.app_context import _App
from nyx.config import Config
from nyx.db import DatabaseLockTimeout, connect
from nyx.enums import EventType
from nyx.events.bus import EventBus
from nyx.runtime import root_event


def message(**changes: Any) -> dict[str, Any]:
    return dict(
        {
            "post_type": "message",
            "message_type": "private",
            "self_id": 123,
            "user_id": 456,
            "message_id": -7,
            "time": int(time.time()),
            "message": [{"type": "text", "data": {"text": "你好"}}],
        },
        **changes,
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"message_type": "group"},
        {"user_id": 999},
        {"self_id": 999},
        {"message": [{"type": "image", "data": {"file": "x"}}]},
        {"message": "[CQ:image,file=x]"},
        {"message": "  "},
    ],
)
async def test_onebot_filters_unaccepted_messages(
    changes: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from nyx.transports.onebot import OneBotAdapter

    db = await connect(":memory:")
    try:
        app = _minimal_app(EventBus(db), Config())
        monkeypatch.setenv("ONEBOT_ACCESS_TOKEN", "fixture-token")
        adapter = OneBotAdapter(app)
        await adapter.on_external(message(**changes))
        assert await app.bus.list_events() == []
    finally:
        await db.close()


def _minimal_app(bus: EventBus, config: Config) -> _App:
    from typing import cast

    config.onebot.self_id, config.onebot.owner_id = 123, 456
    # Only the transport's real bus/database boundary is used in these tests.
    from types import SimpleNamespace

    return cast(
        _App,
        SimpleNamespace(
            bus=bus,
            database=bus._db,
            config=config,
            host=None,
            character_id="nyx",
        ),
    )


async def test_onebot_admits_replayed_message_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from nyx.transports.onebot import OneBotAdapter

    db = await connect(":memory:")
    try:
        app = _minimal_app(EventBus(db), Config())
        monkeypatch.setenv("ONEBOT_ACCESS_TOKEN", "fixture-token")
        adapter = OneBotAdapter(app)
        await adapter.on_external(message(message="a&#91;b&#93;&amp;c"))
        await adapter.on_external(message(message="a&#91;b&#93;&amp;c"))
        events = await app.bus.list_events(event_type=EventType.USER_MESSAGE)
        assert len(events) == 1
        assert events[0].id == "onebot:123:-7"
        assert events[0].content["message"] == "a[b]&c"
        await adapter.quiesce()
        await adapter.on_external(message(message_id=8))
        assert len(await app.bus.list_events()) == 1
    finally:
        await db.close()


async def test_onebot_database_timeout_is_not_a_network_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from nyx.transports.onebot import OneBotAdapter

    db = await connect(":memory:")
    app = _minimal_app(EventBus(db), Config())
    monkeypatch.setenv("ONEBOT_ACCESS_TOKEN", "fixture-token")

    async def server(ws: ServerConnection) -> None:
        async for raw in ws:
            request = json.loads(raw)
            await ws.send(
                json.dumps(
                    {
                        "status": "ok",
                        "retcode": 0,
                        "data": {"user_id": 123},
                        "echo": request["echo"],
                    }
                )
            )

    async def failed_scan() -> None:
        raise TimeoutError("database lock")

    try:
        async with serve(server, "127.0.0.1", 0) as socket:
            port = list(socket.sockets)[0].getsockname()[1]
            app.config.onebot.ws_url = f"ws://127.0.0.1:{port}"
            adapter = OneBotAdapter(app)
            await adapter.outbox.initialize()
            monkeypatch.setattr(adapter.outbox, "scan", failed_scan)
            runner = asyncio.create_task(adapter.run())
            try:
                with pytest.raises(TimeoutError, match="database lock"):
                    await asyncio.wait_for(asyncio.shield(runner), 3)
            finally:
                await adapter.quiesce()
                await asyncio.gather(runner, return_exceptions=True)
    finally:
        await db.close()


@pytest.mark.parametrize("phase", ["scan", "claim", "sent", "sent_reconnect"])
async def test_onebot_recovers_lock_contention_without_resending(
    phase: str, monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    from nyx.transports.onebot import OneBotAdapter

    db = await connect(":memory:")
    db.lock_timeout = 0.01
    monkeypatch.setenv("ONEBOT_ACCESS_TOKEN", "fixture-token")
    adapter = OneBotAdapter(_minimal_app(EventBus(db), Config()))
    await adapter.outbox.initialize()
    event = root_event(EventType.SPEAK, {"content": "once"})
    await adapter._app.bus.publish(event)
    locked, confirmed = asyncio.Event(), asyncio.Event()
    sends: list[str] = []
    operation_name = "sent" if phase == "sent_reconnect" else phase
    operation = getattr(adapter.outbox, operation_name)
    first = True

    async def contested(*args: Any) -> Any:
        nonlocal first
        if first:
            first = False
            await db.lock.acquire()
            locked.set()
        result = await operation(*args)
        if operation_name == "sent":
            confirmed.set()
        return result

    async def emit(event: Any) -> None:
        sends.append(event.id)

    monkeypatch.setattr(adapter.outbox, operation_name, contested)
    monkeypatch.setattr(adapter, "emit", emit)
    if operation_name != "sent":
        original_sent = adapter.outbox.sent

        async def sent(event_id: str) -> None:
            await original_sent(event_id)
            confirmed.set()

        monkeypatch.setattr(adapter.outbox, "sent", sent)
    sender = asyncio.create_task(adapter._send())
    try:
        await asyncio.wait_for(locked.wait(), 2)
        # Wait until an actual lock acquisition has timed out and entered backoff.
        async with asyncio.timeout(2):
            while "OneBot DB lock retry" not in caplog.text:
                await asyncio.sleep(0.01)
        assert not sender.done()
        if phase == "sent_reconnect":
            sender.cancel()
            with pytest.raises(asyncio.CancelledError):
                await sender
            await adapter._release()  # Bounded even while the database is locked.
        db.lock.release()
        if phase == "sent_reconnect":
            sender = asyncio.create_task(adapter._send())
        await asyncio.wait_for(confirmed.wait(), 3)
        assert sends == [event.id]
        async with db.lock:
            row = await (await db.conn.execute(
                "SELECT status FROM transport_outbox WHERE event_id=?", (event.id,)
            )).fetchone()
        assert row is not None and row["status"] == "sent"
    finally:
        sender.cancel()
        await asyncio.gather(sender, return_exceptions=True)
        if db.lock.locked():
            db.lock.release()
        await db.close()


async def test_transaction_lock_timeout_is_distinct_and_cancellable() -> None:
    db = await connect(":memory:")
    db.lock_timeout = 0.01
    try:
        async with db.lock:
            with pytest.raises(DatabaseLockTimeout, match="DB lock acquisition"):
                async with db.transaction():
                    pytest.fail("must not enter transaction")
            waiter = asyncio.create_task(db.lock.acquire())
            waiter.cancel()
            with pytest.raises(asyncio.CancelledError):
                await waiter
        async with db.transaction() as conn:
            await conn.execute("SELECT 1")
    finally:
        await db.close()


async def test_onebot_retries_failed_send_state_without_hiding_network_failure(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    from nyx.transports.onebot import OneBotAdapter, OneBotError

    db = await connect(":memory:")
    db.lock_timeout = 0.01
    monkeypatch.setenv("ONEBOT_ACCESS_TOKEN", "fixture-token")
    adapter = OneBotAdapter(_minimal_app(EventBus(db), Config()))
    await adapter.outbox.initialize()
    event = root_event(EventType.SPEAK, {"content": "retry later"})
    await adapter._app.bus.publish(event)
    locked = asyncio.Event()

    async def disconnected(event: Any) -> None:
        await db.lock.acquire()
        locked.set()
        raise OneBotError("disconnected")

    monkeypatch.setattr(adapter, "emit", disconnected)
    sender = asyncio.create_task(adapter._send())
    try:
        await asyncio.wait_for(locked.wait(), 2)
        async with asyncio.timeout(2):
            while "phase=retry" not in caplog.text:
                await asyncio.sleep(0.01)
        db.lock.release()
        with pytest.raises(OneBotError, match="disconnected"):
            await asyncio.wait_for(asyncio.shield(sender), 3)
        row = await (await db.conn.execute(
            "SELECT status, lease_until FROM transport_outbox WHERE event_id=?",
            (event.id,),
        )).fetchone()
        assert row is not None and row["status"] == "pending"
        assert row["lease_until"] is None
    finally:
        sender.cancel()
        await asyncio.gather(sender, return_exceptions=True)
        if db.lock.locked():
            db.lock.release()
        await db.close()


async def test_onebot_concurrent_network_failure_keeps_database_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from nyx.transports.onebot import OneBotAdapter, OneBotError

    db = await connect(":memory:")
    app = _minimal_app(EventBus(db), Config())
    monkeypatch.setenv("ONEBOT_ACCESS_TOKEN", "fixture-token")
    adapter = OneBotAdapter(app)

    async def login(action: str, params: dict[str, Any]) -> dict[str, Any]:
        return {"data": {"user_id": 123}}

    async def disconnected() -> None:
        raise OneBotError("disconnected")

    async def database_error() -> None:
        raise TimeoutError("database lock")

    monkeypatch.setattr(adapter, "_call", login)
    monkeypatch.setattr(adapter, "_receive", disconnected)
    monkeypatch.setattr(adapter, "_send", database_error)
    try:
        for _ in range(20):
            with pytest.raises(TimeoutError, match="database lock"):
                await adapter._session()
    finally:
        await db.close()


async def test_onebot_ws_reconnects_and_confirms_only_matching_echo(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from nyx.transports.onebot import OneBotAdapter

    db = await connect(":memory:")
    app = _minimal_app(EventBus(db), Config())
    monkeypatch.setenv("ONEBOT_ACCESS_TOKEN", "fixture-token")
    ready, delivered = asyncio.Event(), asyncio.Event()
    sends: list[dict[str, Any]] = []
    connections = 0

    async def server(ws: ServerConnection) -> None:
        nonlocal connections
        connections += 1
        assert ws.request is not None
        assert ws.request.headers["Authorization"] == "Bearer fixture-token"
        async for raw in ws:
            request = json.loads(raw)
            if request["action"] == "get_login_info":
                await ws.send(
                    json.dumps(
                        {
                            "status": "ok",
                            "retcode": 0,
                            "data": {"user_id": 123},
                            "echo": request["echo"],
                        }
                    )
                )
                ready.set()
            else:
                sends.append(request)
                if len(sends) == 1:
                    await ws.close()
                    return
                await ws.send(
                    json.dumps(
                        {
                            "status": "ok",
                            "retcode": 0,
                            "data": {"message_id": 1},
                            "echo": "unrelated",
                        }
                    )
                )
                await asyncio.sleep(0.05)
                async with db.lock:
                    row = await (
                        await db.conn.execute("SELECT status FROM transport_outbox")
                    ).fetchone()
                assert row is not None and row["status"] == "processing"
                await ws.send(
                    json.dumps(
                        {
                            "status": "ok",
                            "retcode": 0,
                            "data": {"message_id": 2},
                            "echo": request["echo"],
                        }
                    )
                )
                delivered.set()

    try:
        async with serve(server, "127.0.0.1", 0) as socket:
            app.config.onebot.ws_url = (
                f"ws://127.0.0.1:{list(socket.sockets)[0].getsockname()[1]}"
            )
            adapter = OneBotAdapter(app)
            await adapter.outbox.initialize()
            runner = asyncio.create_task(adapter.run())
            try:
                await asyncio.wait_for(ready.wait(), 5)
                await app.bus.publish(
                    root_event(
                        EventType.THINK,
                        {"content": "[CQ:x]正文"},
                    )
                )
                await asyncio.wait_for(delivered.wait(), 10)
                row = None
                for _ in range(100):
                    async with db.lock:
                        row = await (
                            await db.conn.execute("SELECT status FROM transport_outbox")
                        ).fetchone()
                    if row is not None and row["status"] == "sent":
                        break
                    await asyncio.sleep(0.02)
                assert row is not None and row["status"] == "sent"
                assert connections >= 2 and len(sends) == 2
                assert sends[-1]["params"] == {
                    "user_id": 456,
                    "message": [
                        {"type": "text", "data": {"text": "💭 内心话\n[CQ:x]正文"}},
                    ],
                }
            finally:
                await adapter.quiesce()
                await asyncio.gather(runner, return_exceptions=True)
    finally:
        await db.close()
