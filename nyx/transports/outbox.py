"""角色内的持久出向队列；网络调用不占有数据库事务。"""

import json
import time

from nyx.db import Database
from nyx.enums import EventType, Source
from nyx.types import Event


class TransportOutbox:
    """Scan committed events and confirm one FIFO recipient independently."""

    def __init__(self, database: Database, transport: str) -> None:
        self._db = database
        self._transport = transport

    async def initialize(self) -> None:
        """Start a new recipient at the tail, retaining existing checkpoints."""
        async with self._db.transaction() as conn:
            await conn.execute(
                "INSERT OR IGNORE INTO transport_checkpoint "
                "SELECT ?, COALESCE(MAX(rowid), 0), COALESCE(MAX(rowid), 0) "
                "FROM event_log",
                (self._transport,),
            )

    async def scan(self) -> None:
        """Enqueue a bounded committed range and move its cursor atomically."""
        async with self._db.transaction() as conn:
            rows = list(
                await (
                    await conn.execute(
                        "SELECT rowid AS sequence, id, type, content FROM event_log "
                        "WHERE rowid > (SELECT scan_rowid FROM transport_checkpoint "
                        "WHERE transport=?) ORDER BY rowid LIMIT 100",
                        (self._transport,),
                    )
                ).fetchall()
            )
            for row in rows:
                if row["type"] not in ("think", "speak", "ask"):
                    continue
                text = json.loads(row["content"]).get("content")
                if isinstance(text, str) and text.strip():
                    await conn.execute(
                        "INSERT OR IGNORE INTO transport_outbox "
                        "(transport, event_id, event_rowid) VALUES (?, ?, ?)",
                        (self._transport, row["id"], row["sequence"]),
                    )
            if rows:
                await conn.execute(
                    "UPDATE transport_checkpoint SET scan_rowid=? WHERE transport=?",
                    (rows[-1]["sequence"], self._transport),
                )

    async def claim(self) -> Event | None:
        """Only the oldest unsent event can hold a sending lease."""
        async with self._db.transaction() as conn:
            row = await (
                await conn.execute(
                    "SELECT o.status, o.available_at, o.lease_until, e.* "
                    "FROM transport_outbox o JOIN event_log e ON e.id=o.event_id "
                    "WHERE o.transport=? AND o.status!='sent' "
                    "ORDER BY o.event_rowid LIMIT 1",
                    (self._transport,),
                )
            ).fetchone()
            now = time.time()
            if (
                row is None
                or row["available_at"] > now
                or (row["status"] == "processing" and row["lease_until"] > now)
            ):
                return None
            await conn.execute(
                "UPDATE transport_outbox SET status='processing', "
                "attempts=attempts+1, lease_until=? WHERE transport=? AND event_id=?",
                (now + 60, self._transport, row["id"]),
            )
            return Event(
                id=row["id"],
                timestamp=row["timestamp"],
                source=Source(row["source"]),
                type=EventType(row["type"]),
                content=json.loads(row["content"]),
                correlation_id=row["correlation_id"],
            )

    async def sent(self, event_id: str) -> None:
        """Confirm sending and its cursor in the same transaction."""
        async with self._db.transaction() as conn:
            await conn.execute(
                "UPDATE transport_outbox SET status='sent', lease_until=NULL, "
                "last_error=NULL WHERE transport=? AND event_id=?",
                (self._transport, event_id),
            )
            await conn.execute(
                "UPDATE transport_checkpoint SET sent_rowid=(SELECT event_rowid "
                "FROM transport_outbox WHERE transport=? AND event_id=?) "
                "WHERE transport=?",
                (self._transport, event_id, self._transport),
            )

    async def retry(self, event_id: str, error: str) -> None:
        """Keep the failed head queued with capped exponential backoff."""
        async with self._db.transaction() as conn:
            row = await (
                await conn.execute(
                    "SELECT attempts FROM transport_outbox "
                    "WHERE transport=? AND event_id=?",
                    (self._transport, event_id),
                )
            ).fetchone()
            assert row is not None
            delay = min(2 ** min(max(row["attempts"] - 1, 0), 5), 30)
            await conn.execute(
                "UPDATE transport_outbox SET status='pending', lease_until=NULL, "
                "available_at=?, last_error=? WHERE transport=? AND event_id=?",
                (time.time() + delay, error, self._transport, event_id),
            )

    async def release(self) -> None:
        """Release leases after this recipient's sending worker has stopped."""
        async with self._db.transaction() as conn:
            await conn.execute(
                "UPDATE transport_outbox SET status='pending', lease_until=NULL "
                "WHERE transport=? AND status='processing'",
                (self._transport,),
            )
