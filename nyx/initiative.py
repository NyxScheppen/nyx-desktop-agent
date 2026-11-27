"""Role-local initiative topics, reminders, and per-occurrence admission."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from typing import Any, Literal
from uuid import uuid4

from nyx.db import Database
from nyx.enums import EventType
from nyx.events.bus import EventBus
from nyx.policy import policy
from nyx.timezone import USER_TIMEZONE
from nyx.types import Event

_LOG = logging.getLogger(__name__)
DEFAULT_COOLDOWN = policy("initiative", "defaults.cooldown_minutes", int)
MIN_COOLDOWN = policy("initiative", "cooldown_min_minutes", int)
MAX_COOLDOWN = policy("initiative", "cooldown_max_minutes", int)

Origin = Literal["user", "autonomous"]
Action = Literal["reminder", "reading", "continue_chat"]
Recurrence = Literal["once", "daily", "weekly", "workdays"]


def local_time(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, USER_TIMEZONE).isoformat(
        timespec="minutes"
    )


def parse_local_time(value: str, now: float) -> float:
    try:
        local = datetime.fromisoformat(value)
        if local.tzinfo is None and local.isoformat(timespec="minutes") == value:
            timestamp = local.replace(tzinfo=USER_TIMEZONE).timestamp()
            if timestamp > now:
                return timestamp
    except (ValueError, OverflowError, OSError):
        pass
    raise ValueError(policy("initiative", "copy.invalid_time", str))


@dataclass(frozen=True)
class Topic:
    book_id: str | None = None
    paragraph: int | None = None
    event_id: str | None = None

    @property
    def key(self) -> str:
        if self.book_id is not None and self.event_id is not None:
            return Topic(self.book_id, self.paragraph).encode()
        return self.encode()

    def encode(self) -> str:
        return json.dumps(asdict(self), separators=(",", ":"))

    @classmethod
    def decode(cls, value: str) -> Topic:
        return cls(**json.loads(value))

    def matches(self, other: Topic) -> bool:
        if self.book_id is not None and self.book_id == other.book_id:
            return (
                self.paragraph is None
                or other.paragraph is None
                or self.paragraph == other.paragraph
            )
        return self.event_id is not None and self.event_id == other.event_id


@dataclass(frozen=True)
class ScheduledInteraction:
    id: str
    origin: Origin
    action: Action
    note: str
    topic: Topic
    source_event_id: str
    timezone: str
    due_at: float
    recurrence: Recurrence
    skip_if_read: bool
    status: str
    version: int
    claim_id: str | None
    skip_if_read_today: bool = False

    @property
    def checks_read_position(self) -> bool:
        return self.skip_if_read or (
            self.origin == "autonomous" and self.action == "reading"
        )

    @property
    def deadline(self) -> float:
        minutes = (
            policy("initiative", "scheduler.user_window_minutes", int)
            if self.origin == "user"
            else policy("initiative", "scheduler.autonomous_window_minutes", int)
        )
        return self.due_at + timedelta(minutes=minutes).total_seconds()

    def result(self) -> dict[str, object]:
        return {
            "id": self.id,
            "origin": self.origin,
            "action": self.action,
            "note": self.note,
            "book_id": self.topic.book_id,
            "paragraph": self.topic.paragraph,
            "source_event_id": self.source_event_id,
            "next_local_time": local_time(self.due_at)
            if self.status in ("active", "paused")
            else None,
            "timezone": self.timezone,
            "recurrence": self.recurrence,
            "skip_if_read": self.skip_if_read,
            "skip_if_read_today": self.skip_if_read_today,
            "status": self.status,
            "version": self.version,
        }


def _item(row: Any) -> ScheduledInteraction:
    return ScheduledInteraction(
        row["id"],
        row["origin"],
        row["action"],
        row["note"],
        Topic.decode(row["reference"]),
        row["source_event_id"],
        row["timezone"],
        row["due_at"],
        row["recurrence"],
        bool(row["skip_if_read"]),
        row["status"],
        row["version"],
        row["claim_id"],
        bool(row["skip_if_read_today"]),
    )


def _period(recurrence: Recurrence) -> timedelta | None:
    if recurrence == "once":
        return None
    return timedelta(weeks=1) if recurrence == "weekly" else timedelta(days=1)


def _working_due(due_at: float, *, direction: int = 1) -> float:
    days = policy("initiative", "scheduler.working_days", list[int])
    if not days or any(day not in range(7) for day in days):
        raise ValueError(policy("initiative", "copy.invalid_item", str))
    due = datetime.fromtimestamp(due_at, USER_TIMEZONE)
    while due.weekday() not in days:
        due += timedelta(days=direction)
    return due.timestamp()


def _next_due(due_at: float, recurrence: Recurrence, now: float) -> float | None:
    period = _period(recurrence)
    if period is None:
        return None
    local = datetime.fromtimestamp(due_at, USER_TIMEZONE)
    periods = max(1, (datetime.fromtimestamp(now, USER_TIMEZONE) - local) // period + 1)
    following = (local + period * periods).timestamp()
    return _working_due(following) if recurrence == "workdays" else following


class Initiative:
    def __init__(self, db: Database, bus: EventBus, autonomous_enabled: bool) -> None:
        self.db = db
        self.bus = bus
        self.autonomous_enabled = autonomous_enabled
        self.expression_lock = asyncio.Lock()

    @asynccontextmanager
    async def _read(self) -> AsyncGenerator[None]:
        if self.db.in_transaction:
            yield
        else:
            async with self.db.lock:
                yield

    async def cooldown(self) -> int:
        async with self._read():
            row = await (
                await self.db.conn.execute(
                    "SELECT cooldown_minutes FROM initiative_preferences WHERE id=1"
                )
            ).fetchone()
        return DEFAULT_COOLDOWN if row is None else int(row["cooldown_minutes"])

    async def set_cooldown(self, minutes: int) -> int:
        if not MIN_COOLDOWN <= minutes <= MAX_COOLDOWN:
            raise ValueError(policy("initiative", "copy.invalid_cooldown", str))
        async with self.db.transaction() as conn:
            await conn.execute(
                "INSERT INTO initiative_preferences VALUES (1, ?) "
                "ON CONFLICT (id) DO UPDATE SET "
                "cooldown_minutes=excluded.cooldown_minutes",
                (minutes,),
            )
        return minutes

    async def busy(self) -> bool:
        """A committed user input still awaits its terminal expression."""
        async with self._read():
            row = await (
                await self.db.conn.execute(
                    "SELECT 1 FROM event_delivery d "
                    "JOIN event_log e ON e.id=d.event_id "
                    "WHERE d.consumer_id='expression.user_message' "
                    "AND d.status IN ('pending', 'processing', 'retry_wait') "
                    "AND NOT EXISTS (SELECT 1 FROM event_effect f "
                    "WHERE f.event_id=e.id "
                    "AND f.consumer_id=d.consumer_id) "
                    "AND NOT EXISTS (SELECT 1 FROM event_log r "
                    "WHERE r.correlation_id=e.id "
                    "AND r.type IN ('speak', 'ask')) LIMIT 1"
                )
            ).fetchone()
        return row is not None

    async def topic_evidence(self, topic: Topic) -> str | None:
        limit = policy("initiative", "topic.evidence_chars", int)
        if topic.book_id is not None:
            async with self._read():
                row = await (
                    await self.db.conn.execute(
                        "SELECT b.title, p.text FROM books b LEFT JOIN paragraphs p "
                        'ON p.book_id=b.id AND p."index"=? WHERE b.id=?',
                        (topic.paragraph, topic.book_id),
                    )
                ).fetchone()
            if row is None or (topic.paragraph is not None and row["text"] is None):
                return None
            return json.dumps(
                {
                    "title": row["title"],
                    "paragraph": topic.paragraph,
                    "text": str(row["text"])[:limit]
                    if row["text"] is not None
                    else None,
                },
                ensure_ascii=False,
            )
        if topic.event_id is None:
            return None
        event = await self.bus.get_event(topic.event_id)
        if event is None or event.type is not EventType.USER_MESSAGE:
            return None
        related = await self.bus.list_events(correlation_id=event.correlation_id)
        reply = next(
            (e for e in related if e.type in (EventType.SPEAK, EventType.ASK)), None
        )
        return json.dumps(
            {
                "user": str(event.content.get("message", ""))[:limit],
                "nyx": str(reply.content.get("content", ""))[:limit] if reply else None,
            },
            ensure_ascii=False,
        )

    async def resolve_book(self, book: str) -> str | None:
        async with self._read():
            rows = list(
                await (
                    await self.db.conn.execute(
                        "SELECT id FROM books WHERE id=? OR lower(title)=lower(?) "
                        "ORDER BY CASE WHEN id=? THEN 0 ELSE 1 END LIMIT 2",
                        (book, book, book),
                    )
                ).fetchall()
            )
        if rows and (len(rows) == 1 or rows[0]["id"] == book):
            return str(rows[0]["id"])
        return None

    async def user_reached(self, topic: Topic) -> bool:
        if topic.book_id is None or topic.paragraph is None:
            return False
        async with self._read():
            row = await (
                await self.db.conn.execute(
                    "SELECT user_position FROM reading_progress WHERE book_id=? "
                    "AND (user_position_recorded=1 OR user_position>1)",
                    (topic.book_id,),
                )
            ).fetchone()
        return row is not None and int(row["user_position"]) >= topic.paragraph

    async def already_read(self, item: ScheduledInteraction, topic: Topic) -> bool:
        if item.checks_read_position and await self.user_reached(topic):
            return True
        if not item.skip_if_read_today or topic.book_id is None:
            return False
        due = datetime.fromtimestamp(item.due_at, USER_TIMEZONE)
        start = due.replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + timedelta(days=1)
        async with self._read():
            row = await (await self.db.conn.execute(
                "SELECT 1 FROM reading_progress WHERE book_id=? AND "
                "last_user_read_at>=? AND last_user_read_at<?",
                (topic.book_id, start.timestamp(), end.timestamp()),
            )).fetchone()
        return row is not None

    async def was_sent(
        self,
        topic: Topic,
        *,
        reading_questions: bool = False,
    ) -> bool:
        async with self._read():
            row = await (
                await self.db.conn.execute(
                    "SELECT 1 FROM initiative_topic_sent WHERE topic_key=?",
                    (topic.key,),
                )
            ).fetchone()
        if row is not None:
            return True
        if not reading_questions or topic.book_id is None or topic.paragraph is None:
            return False
        return bool(
            await self.bus.list_events(
                limit=1,
                event_type=EventType.READING_QUESTION,
                correlation_id=f"{topic.book_id}:{topic.paragraph}",
            )
        )

    async def recent_enhanced(self, now: float) -> bool:
        cutoff = (
            now
            - timedelta(
                hours=policy("initiative", "topic.send_interval_hours", int)
            ).total_seconds()
        )
        async with self._read():
            row = await (
                await self.db.conn.execute(
                    "SELECT 1 FROM initiative_topic_sent WHERE enhanced_at>? LIMIT 1",
                    (cutoff,),
                )
            ).fetchone()
        return row is not None

    async def record_topic(self, topic: Topic, event: Event, enhanced: bool) -> None:
        await self.db.conn.execute(
            "INSERT INTO initiative_topic_sent (topic_key, event_id, enhanced_at) "
            "VALUES (?, ?, ?) ON CONFLICT (topic_key) DO UPDATE SET "
            "event_id=excluded.event_id, "
            "enhanced_at=COALESCE(initiative_topic_sent.enhanced_at, "
            "excluded.enhanced_at)",
            (topic.key, event.id, event.timestamp if enhanced else None),
        )

    async def eligible(self, now: float) -> bool:
        hour = datetime.fromtimestamp(now, USER_TIMEZONE).hour
        start = policy("initiative", "topic.start_hour", int)
        end = policy("initiative", "topic.end_hour", int)
        if not start <= hour < end or await self.recent_enhanced(now):
            return False
        latest = await self.bus.list_events(limit=1, event_type=EventType.USER_MESSAGE)
        idle = timedelta(
            hours=policy("initiative", "topic.idle_hours", int)
        ).total_seconds()
        return bool(latest) and now - latest[0].timestamp >= idle

    async def latest_book_topic(self) -> Topic | None:
        latest = await self.bus.list_events(
            limit=1, event_type=EventType.READING_PROGRESS
        )
        if latest:
            book = latest[0].content.get("book_id")
            position = latest[0].content.get("user_position")
            if isinstance(book, str) and isinstance(position, int):
                return Topic(book_id=book, paragraph=position + 1)
        return None

    async def candidate(self, now: float) -> Topic | None:
        if not await self.eligible(now):
            _LOG.info(
                policy("initiative", "copy.admission_log", str),
                "topic_ineligible",
                None,
            )
            return None
        age = timedelta(
            hours=policy("initiative", "topic.max_age_hours", int)
        ).total_seconds()
        limit = policy("initiative", "topic.candidate_limit", int)
        for event in await self.bus.list_events(
            limit=limit, event_type=EventType.READING_PROGRESS
        ):
            if event.timestamp < now - age:
                break
            book = event.content.get("book_id")
            nyx = event.content.get("nyx_position")
            user = event.content.get("user_position")
            if (
                not isinstance(book, str)
                or not isinstance(nyx, int)
                or not isinstance(user, int)
            ):
                continue
            topic = Topic(book_id=book, paragraph=min(nyx, user))
            if (
                min(nyx, user) >= 1
                and not await self.was_sent(topic, reading_questions=True)
                and await self.topic_evidence(topic) is not None
            ):
                _LOG.info(
                    policy("initiative", "copy.topic_candidate_log", str),
                    "reading",
                    topic.key,
                )
                return topic
        followup = policy("initiative", "patterns.followup", re.Pattern[str])
        for event in await self.bus.list_events(
            limit=limit, event_type=EventType.USER_MESSAGE
        ):
            if event.timestamp < now - age:
                break
            text = event.content.get("message")
            if not isinstance(text, str) or not followup.search(text):
                continue
            if not any(
                e.type is EventType.SPEAK
                for e in await self.bus.list_events(correlation_id=event.correlation_id)
            ):
                continue
            topic = Topic(event_id=event.id)
            if not await self.was_sent(topic):
                _LOG.info(
                    policy("initiative", "copy.topic_candidate_log", str),
                    "conversation",
                    event.id,
                )
                return topic
        _LOG.info(policy("initiative", "copy.admission_log", str), "no_topic", None)
        return None

    async def create(
        self,
        origin: Origin,
        action: Action,
        local: str,
        recurrence: Recurrence,
        note: str,
        topic: Topic,
        source: Event,
        skip_if_read: bool,
        skip_if_read_today: bool = False,
    ) -> ScheduledInteraction:
        due = parse_local_time(local, time.time())
        if (
            origin not in ("user", "autonomous")
            or recurrence not in ("once", "daily", "weekly", "workdays")
            or action not in ("reminder", "reading", "continue_chat")
            or (skip_if_read and (topic.book_id is None or topic.paragraph is None))
            or (skip_if_read_today and (action != "reading" or topic.book_id is None))
            or (action == "continue_chat" and topic.event_id is None)
            or (
                origin == "autonomous"
                and (
                    recurrence != "once"
                    or action == "reminder"
                    or (action == "reading" and topic.paragraph is None)
                )
            )
        ):
            raise ValueError(policy("initiative", "copy.invalid_item", str))
        if recurrence == "workdays":
            due = _working_due(due)
        if source.type not in (EventType.USER_MESSAGE, EventType.ACTIVITY_END):
            raise ValueError(policy("initiative", "copy.missing_source", str))
        if origin == "autonomous" and not self.autonomous_enabled:
            raise ValueError(policy("initiative", "copy.autonomous_disabled", str))
        if origin == "user" and (
            source.type is not EventType.USER_MESSAGE
            or not policy("initiative", "patterns.explicit", re.Pattern[str]).search(
                str(source.content.get("message", ""))
            )
        ):
            raise ValueError(policy("initiative", "copy.missing_request", str))
        identifier = str(uuid4())
        async with self.db.transaction() as conn:
            if (topic.book_id is not None or topic.event_id is not None) and (
                await self.topic_evidence(topic) is None
            ):
                raise ValueError(policy("initiative", "copy.missing_source", str))
            if source.type is EventType.ACTIVITY_END:
                existing = await (
                    await conn.execute(
                        "SELECT 1 FROM scheduled_interaction "
                        "WHERE source_event_id=? LIMIT 1",
                        (source.id,),
                    )
                ).fetchone()
                if existing is not None:
                    raise ValueError(policy("initiative", "copy.autonomous_busy", str))
            cursor = await conn.execute(
                "INSERT INTO scheduled_interaction "
                "(id, origin, action, note, reference, source_event_id, "
                "timezone, due_at, "
                "recurrence, skip_if_read, status, version, created_at, "
                "skip_if_read_today) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', 1, ?, ?) "
                "ON CONFLICT DO NOTHING",
                (
                    identifier,
                    origin,
                    action,
                    note,
                    topic.encode(),
                    source.id,
                    USER_TIMEZONE.tzname(None),
                    due,
                    recurrence,
                    int(skip_if_read),
                    time.time(),
                    int(skip_if_read_today),
                ),
            )
            if cursor.rowcount != 1:
                raise ValueError(policy("initiative", "copy.autonomous_busy", str))
            return await self.get(identifier)

    async def get(self, identifier: str) -> ScheduledInteraction:
        async with self._read():
            row = await (
                await self.db.conn.execute(
                    "SELECT * FROM scheduled_interaction WHERE id=?", (identifier,)
                )
            ).fetchone()
        if row is None:
            raise ValueError(policy("initiative", "copy.no_item", str))
        return _item(row)

    async def list_items(self) -> list[dict[str, object]]:
        async with self._read():
            rows = await (
                await self.db.conn.execute(
                    "SELECT s.*, (SELECT o.outcome FROM scheduled_occurrence o "
                    "WHERE o.item_id=s.id ORDER BY o.due_at DESC LIMIT 1) "
                    "AS last_outcome "
                    "FROM scheduled_interaction s ORDER BY "
                    "CASE WHEN status IN ('active', 'paused') THEN 0 ELSE 1 END, "
                    "due_at, id"
                )
            ).fetchall()
        return [
            _item(row).result() | {"last_outcome": row["last_outcome"]} for row in rows
        ]

    async def modify(
        self, identifier: str, version: int, operation: str, local: str | None = None
    ) -> ScheduledInteraction:
        now = time.time()
        async with self.db.transaction() as conn:
            item = await self.get(identifier)
            status = item.status
            due = item.due_at
            if item.version != version:
                raise ValueError(policy("initiative", "copy.no_item", str))
            if operation == "skip_today":
                current = datetime.fromtimestamp(now, USER_TIMEZONE)
                due_today = datetime.fromtimestamp(due, USER_TIMEZONE).date()
                unavailable = policy("initiative", "copy.no_occurrence_today", str)
                if item.action != "reading" or item.origin != "user":
                    raise ValueError(unavailable)
                if status != "active" or due_today != current.date():
                    start = current.replace(hour=0, minute=0, second=0, microsecond=0)
                    end = start + timedelta(days=1)
                    updated = await conn.execute(
                        "UPDATE scheduled_occurrence SET outcome='declined' "
                        "WHERE item_id=? AND due_at>=? AND due_at<? "
                        "AND outcome IN ('sent', 'declined')",
                        (identifier, start.timestamp(), end.timestamp()),
                    )
                    if updated.rowcount:
                        return item
                    raise ValueError(unavailable)
                operation = "skip"
            if status not in ("active", "paused"):
                raise ValueError(policy("initiative", "copy.no_item", str))
            if operation == "reschedule" and local is not None:
                due = parse_local_time(local, now)
            elif operation == "pause":
                status = "paused"
            elif operation == "cancel":
                status = "cancelled"
            elif operation == "skip":
                following = _next_due(due, item.recurrence, now)
                if following is None:
                    status = "completed"
                else:
                    due = following
            elif operation == "resume":
                status = "active"
                if local is not None:
                    due = parse_local_time(local, now)
                elif due <= now:
                    next_due = _next_due(due, item.recurrence, now)
                    if next_due is None:
                        raise ValueError(policy("initiative", "copy.invalid_time", str))
                    due = next_due
            else:
                raise ValueError(policy("initiative", "copy.invalid_item", str))
            if item.recurrence == "workdays":
                due = _working_due(due)
            changed = await conn.execute(
                "UPDATE scheduled_interaction SET status=?, due_at=?, "
                "version=version+1, "
                "claim_id=NULL, claim_until=NULL WHERE id=? AND version=?",
                (status, due, identifier, version),
            )
            if changed.rowcount != 1:
                raise ValueError(policy("initiative", "copy.no_item", str))
            if operation == "skip":
                await conn.execute(
                    "INSERT INTO scheduled_occurrence VALUES (?, ?, 'skipped', NULL)",
                    (item.id, item.due_at),
                )
            return await self.get(identifier)

    async def has_autonomous(self) -> bool:
        async with self._read():
            row = await (
                await self.db.conn.execute(
                    "SELECT 1 FROM scheduled_interaction WHERE origin='autonomous' "
                    "AND status IN ('active', 'paused') LIMIT 1"
                )
            ).fetchone()
        return row is not None

    async def due(self, now: float) -> list[ScheduledInteraction]:
        async with self._read():
            rows = await (
                await self.db.conn.execute(
                    "SELECT * FROM scheduled_interaction "
                    "WHERE status='active' AND due_at<=? "
                    "ORDER BY CASE WHEN origin='user' THEN 0 ELSE 1 END, due_at, id",
                    (now,),
                )
            ).fetchall()
        return [_item(row) for row in rows]

    async def claim(
        self, item: ScheduledInteraction, now: float
    ) -> ScheduledInteraction | None:
        claim_id = str(uuid4())
        async with self.db.transaction() as conn:
            cursor = await conn.execute(
                "UPDATE scheduled_interaction SET claim_id=?, claim_until=? WHERE id=? "
                "AND version=? AND status='active' "
                "AND (claim_until IS NULL OR claim_until<=?)",
                (
                    claim_id,
                    now + policy("initiative", "scheduler.claim_seconds", int),
                    item.id,
                    item.version,
                    now,
                ),
            )
            return await self.get(item.id) if cursor.rowcount == 1 else None

    async def release(self, item: ScheduledInteraction) -> None:
        async with self.db.transaction() as conn:
            await conn.execute(
                "UPDATE scheduled_interaction SET claim_id=NULL, claim_until=NULL "
                "WHERE id=? AND claim_id=?",
                (item.id, item.claim_id),
            )

    async def finish(
        self,
        item: ScheduledInteraction,
        outcome: str,
        now: float,
        event_id: str | None = None,
    ) -> bool:
        """Advance the occurrence inside the caller's expression transaction."""
        next_due = _next_due(item.due_at, item.recurrence, now)
        results = [(item.id, item.due_at, outcome, event_id)]
        period = _period(item.recurrence)
        if next_due is not None and period is not None:
            interval = period.total_seconds()
            latest_due = next_due - interval
            if item.recurrence == "workdays":
                latest_due = _working_due(latest_due, direction=-1)
            if outcome == "missed" and now < latest_due + item.deadline - item.due_at:
                next_due = latest_due
            skipped = round((next_due - item.due_at) / interval)
            for index in range(1, skipped):
                missed = item.due_at + index * interval
                if item.recurrence != "workdays" or _working_due(missed) == missed:
                    results.append((item.id, missed, "missed", None))
        conn = self.db.conn
        cursor = await conn.execute(
            "UPDATE scheduled_interaction SET due_at=?, status=?, version=version+1, "
            "claim_id=NULL, claim_until=NULL WHERE id=? AND version=? AND "
            "status='active' AND claim_id=?",
            (
                item.due_at if next_due is None else next_due,
                "completed" if next_due is None else "active",
                item.id,
                item.version,
                item.claim_id,
            ),
        )
        if cursor.rowcount != 1:
            return False
        await conn.executemany(
            "INSERT INTO scheduled_occurrence VALUES (?, ?, ?, ?)",
            results,
        )
        return True


def scheduled_topic(item: ScheduledInteraction) -> Topic | None:
    """Only a real book or conversation can participate in topic matching."""
    topic = item.topic
    return topic if topic.book_id is not None or topic.event_id is not None else None
