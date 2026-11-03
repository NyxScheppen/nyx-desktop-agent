import time
from pathlib import Path

import pytest

from nyx.db import connect
from nyx.enums import EventType
from nyx.events.bus import EventBus
from nyx.runtime import root_event


async def test_outbox_skips_history_and_retries_in_commit_order() -> None:
    from nyx.transports.outbox import TransportOutbox

    db = await connect(":memory:")
    bus = EventBus(db)
    try:
        await bus.publish(root_event(EventType.SPEAK, {"content": "old"}))
        await bus.publish(root_event(EventType.THINK, {"content": "old thought"}))
        outbox = TransportOutbox(db, "onebot:123:456")
        await outbox.initialize()
        await bus.publish(root_event(EventType.MUTTER, {"content": "private"}))
        first = root_event(EventType.THINK, {"content": "first"})
        first.timestamp = 1
        second = root_event(EventType.ASK, {"content": "second"})
        second.timestamp = 0
        await bus.publish(first)
        await bus.publish(second)
        await outbox.scan()
        claimed = await outbox.claim()
        assert claimed is not None and claimed.id == first.id
        await outbox.retry(first.id, "TimeoutError")
        assert await outbox.claim() is None
        async with db.transaction() as conn:
            await conn.execute("UPDATE transport_outbox SET available_at=0")
        claimed = await outbox.claim()
        assert claimed is not None and claimed.id == first.id
        await outbox.sent(first.id)
        claimed = await outbox.claim()
        assert claimed is not None and claimed.id == second.id
        await outbox.sent(second.id)
        assert await outbox.claim() is None
    finally:
        await db.close()


async def test_outbox_recovers_expired_lease_without_resending_confirmed(
    tmp_path: Path,
) -> None:
    from nyx.transports.outbox import TransportOutbox

    path = str(tmp_path / "role.db")
    db = await connect(path)
    event = root_event(EventType.THINK, {"content": "queued"})
    try:
        outbox = TransportOutbox(db, "onebot:123:456")
        await outbox.initialize()
        await EventBus(db).publish(event)
        await outbox.scan()
        await outbox.claim()
    finally:
        await db.close()
    db = await connect(path)
    try:
        outbox = TransportOutbox(db, "onebot:123:456")
        await outbox.initialize()
        assert await outbox.claim() is None
        async with db.transaction() as conn:
            await conn.execute(
                "UPDATE transport_outbox SET lease_until=?", (time.time() - 1,)
            )
        claimed = await outbox.claim()
        assert claimed is not None and claimed.id == event.id
        await outbox.sent(event.id)
        await outbox.scan()
        assert await outbox.claim() is None
        other = TransportOutbox(db, "onebot:123:789")
        await other.initialize()
        await other.scan()
        assert await other.claim() is None
    finally:
        await db.close()


async def test_outbox_rolls_back_queue_and_confirmation_with_cursor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from nyx.transports.outbox import TransportOutbox

    db = await connect(":memory:")
    try:
        outbox = TransportOutbox(db, "onebot:123:456")
        await outbox.initialize()
        event = root_event(EventType.SPEAK, {"content": "atomic"})
        await EventBus(db).publish(event)

        original_execute = db.conn.execute

        async def fail_checkpoint_update(
            sql: str,
            parameters: tuple[object, ...] | list[object] | None = None,
        ) -> object:
            if sql.startswith("UPDATE transport_checkpoint"):
                raise RuntimeError("injected")
            return await original_execute(sql, parameters)

        monkeypatch.setattr(db.conn, "execute", fail_checkpoint_update)
        with pytest.raises(RuntimeError, match="injected"):
            await outbox.scan()
        async with db.lock:
            rows = await (
                await db.conn.execute("SELECT * FROM transport_outbox")
            ).fetchall()
        assert rows == []
        monkeypatch.setattr(db.conn, "execute", original_execute)
        await outbox.scan()
        await outbox.claim()
        monkeypatch.setattr(db.conn, "execute", fail_checkpoint_update)
        with pytest.raises(RuntimeError, match="injected"):
            await outbox.sent(event.id)
        async with db.lock:
            row = await (
                await db.conn.execute("SELECT status FROM transport_outbox")
            ).fetchone()
        assert row is not None and row["status"] == "processing"
        monkeypatch.setattr(db.conn, "execute", original_execute)
        await outbox.release()
    finally:
        await db.close()
