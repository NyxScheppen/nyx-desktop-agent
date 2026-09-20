import asyncio
import json
import logging
import time
from collections import defaultdict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import cast

import aiosqlite

from nyx.db import Database
from nyx.enums import EventType, Source, TickType
from nyx.events.routing import ROUTE_SPECS, RouteSpec, routes_for_event
from nyx.types import Event

Handler = Callable[[Event], Awaitable[None]]

_DELIVERY_MAX_ATTEMPTS = 5
_PERSIST_MAX_ATTEMPTS = _DELIVERY_MAX_ATTEMPTS
_DELIVERY_RETRY_DELAYS = (1.0, 2.0, 4.0, 8.0, 30.0)
_DELIVERY_LEASE_SECONDS = 300.0
_DRAIN_TIMEOUT_SECONDS = 15.0
_ADMISSION_TIMEOUT_SECONDS = 3.0
_WAKE_QUEUE_SIZE = 1024
_SCAN_INTERVAL_SECONDS = 0.1
_TERMINAL_DELIVERY_STATES = ("succeeded", "dead_letter")


class _CompatibilityQueue:
    """Expose the old join/qsize view without making it the source of truth."""

    def __init__(self) -> None:
        self._unfinished = 0
        self._finished = asyncio.Event()
        self._finished.set()

    def track(self, delivery_count: int) -> None:
        self._unfinished += 1
        self._finished.clear()
        if delivery_count == 0:
            self.task_done()

    def task_done(self) -> None:
        if self._unfinished <= 0:
            raise ValueError("task_done() called too many times")
        self._unfinished -= 1
        if self._unfinished == 0:
            self._finished.set()

    async def join(self) -> None:
        await self._finished.wait()

    def qsize(self) -> int:
        return self._unfinished


@dataclass(frozen=True)
class SubscriptionToken:
    """Stable handle returned by a durable consumer registration."""

    consumer_id: str
    handler: Handler


class EventAdmissionError(RuntimeError):
    """The event could not be durably accepted."""


class EventBusClosedError(EventAdmissionError):
    """The event bus is quiescing or already closed."""


class EventBus:
    """Durable event log with independently replayable consumer deliveries."""

    def __init__(self, db: Database) -> None:
        self._db = db
        self._logger = logging.getLogger(__name__)
        self._handlers: dict[str, Handler] = {}
        self._legacy_specs: dict[EventType, list[RouteSpec]] = defaultdict(list)
        self._specs: dict[str, RouteSpec] = {}
        self._queue = _CompatibilityQueue()
        self._compat_pending: dict[str, int] = {}
        self._sse_sinks: list[asyncio.Queue[Event]] = []
        self._wake_queue: asyncio.Queue[str] = asyncio.Queue(
            maxsize=_WAKE_QUEUE_SIZE
        )
        self._wake_events: dict[str, asyncio.Event] = {}
        self._workers: dict[str, asyncio.Task[None]] = {}
        self._stop_event = asyncio.Event()
        self._accepting = True
        self._running = False
        self._closed = False
        self.persisted_count = 0

    @property
    def accepting(self) -> bool:
        """Return whether new root events may be admitted."""
        return self._accepting and not self._closed and not self._db.is_closed

    @property
    def consumer_ids(self) -> frozenset[str]:
        """Return the currently registered durable consumers."""
        return frozenset(self._handlers)

    def validate_routes(self) -> None:
        """Fail fast when a declared consumer has no runtime handler."""
        expected = {spec.consumer_id for spec in ROUTE_SPECS}
        registered = {
            spec.consumer_id
            for spec in self._specs.values()
            if not spec.consumer_id.startswith("legacy.")
        }
        if expected != registered:
            raise RuntimeError(
                f"事件路由注册不一致: expected={sorted(expected)}, "
                f"registered={sorted(registered)}"
            )

    def subscribe(
        self, spec_or_event_type: RouteSpec | EventType, handler: Handler
    ) -> SubscriptionToken:
        """Register a route; accept the old event-type form for compatibility."""
        if isinstance(spec_or_event_type, EventType):
            specs = self._legacy_specs[spec_or_event_type]
            for spec in specs:
                if self._handlers[spec.consumer_id] == handler:
                    return SubscriptionToken(spec.consumer_id, handler)
            index = len(specs)
            spec = RouteSpec(
                spec_or_event_type,
                f"legacy.{spec_or_event_type.value}.{index}",
                "legacy",
                f"legacy_{spec_or_event_type.value}_{index}",
                max_attempts=_DELIVERY_MAX_ATTEMPTS,
            )
            specs.append(spec)
            self._register(spec, handler)
            return SubscriptionToken(spec.consumer_id, handler)

        spec = spec_or_event_type
        existing = self._handlers.get(spec.consumer_id)
        if existing is not None:
            if existing != handler:
                raise ValueError(
                    f"consumer {spec.consumer_id!r} already has another handler"
                )
            return SubscriptionToken(spec.consumer_id, handler)
        self._register(spec, handler)
        return SubscriptionToken(spec.consumer_id, handler)

    def unsubscribe(self, token: SubscriptionToken) -> None:
        """Remove a consumer registration; repeated removal is idempotent."""
        handler = self._handlers.get(token.consumer_id)
        if handler is None:
            return
        if handler != token.handler:
            raise ValueError(
                f"consumer {token.consumer_id!r} token does not match handler"
            )
        self._handlers.pop(token.consumer_id, None)
        self._specs.pop(token.consumer_id, None)
        for event_type, specs in self._legacy_specs.items():
            self._legacy_specs[event_type] = [
                spec for spec in specs if spec.consumer_id != token.consumer_id
            ]
        self._wake_events.pop(token.consumer_id, None)
        task = self._workers.pop(token.consumer_id, None)
        if task is not None and not task.done():
            task.cancel()

    def _register(self, spec: RouteSpec, handler: Handler) -> None:
        self._handlers[spec.consumer_id] = handler
        self._specs[spec.consumer_id] = spec
        self._wake_events.setdefault(spec.consumer_id, asyncio.Event())
        if self._running:
            self._start_worker(spec.consumer_id)

    def add_sse_sink(self, sink: asyncio.Queue[Event]) -> None:
        self._sse_sinks.append(sink)

    def remove_sse_sink(self, sink: asyncio.Queue[Event]) -> None:
        if sink in self._sse_sinks:
            self._sse_sinks.remove(sink)

    async def publish(self, event: Event) -> None:
        """Durably admit an event before returning to the caller."""
        if self._closed or self._db.is_closed:
            raise EventBusClosedError("事件总线不接受新事件")
        if not self._accepting and event.source is not Source.INTERNAL:
            raise EventBusClosedError("事件总线不接受新事件")
        if self._db.failure_state == "open":
            raise EventAdmissionError("数据库熔断中，事件未受理")

        try:
            inserted, consumer_ids = await asyncio.wait_for(
                self._admit(event), timeout=_ADMISSION_TIMEOUT_SECONDS
            )
        except asyncio.CancelledError:
            raise
        except Exception as error:
            self._db.record_failure()
            raise EventAdmissionError("事件持久化失败，事件未受理") from error
        else:
            self._db.record_success()

        if inserted:
            self.persisted_count += 1
            self._queue.track(len(consumer_ids))
            if consumer_ids:
                self._compat_pending[event.id] = len(consumer_ids)
            self._broadcast(event)
        for consumer_id in consumer_ids:
            self._wake(consumer_id)

    async def run(self) -> None:
        """Recover durable deliveries, then keep workers alive until stopped."""
        if self._closed:
            return
        self._running = True
        self._stop_event.clear()
        await self.recover_deliveries()
        for consumer_id in self._handlers:
            self._start_worker(consumer_id)
            self._wake_events[consumer_id].set()
        dispatcher = asyncio.create_task(self._dispatch_wakes())
        try:
            await self._stop_event.wait()
        finally:
            dispatcher.cancel()
            await asyncio.gather(dispatcher, return_exceptions=True)
            await self._stop_workers(cancel=True)
            self._running = False

    async def quiesce(self) -> None:
        """Stop accepting new root events while allowing current work to finish."""
        self._accepting = False

    async def drain(self, timeout: float = _DRAIN_TIMEOUT_SECONDS) -> bool:
        """Finish ready deliveries within a bound; leave unfinished work durable."""
        await self.quiesce()
        if not self._running:
            return await self._has_no_ready_deliveries()
        try:
            await asyncio.wait_for(self._wait_until_idle(), timeout=timeout)
        except asyncio.TimeoutError:
            self._logger.warning("事件总线 drain 超时，未完成 delivery 保留待恢复")
            return False
        finally:
            self._stop_event.set()
        return True

    async def stop(self) -> None:
        """Stop workers after a caller has completed quiescing and draining."""
        self._stop_event.set()
        await self._stop_workers()

    async def close(self) -> None:
        """Drain workers and close the shared database exactly once."""
        if self._closed:
            return
        self._accepting = False
        drained = await self.drain()
        if not drained:
            await self._stop_workers(cancel=True)
            self._stop_event.set()
        else:
            await self.stop()
        self._closed = True
        await self._db.close()

    async def recover_deliveries(self) -> None:
        """Recover expired leases and fill deliveries missing after a crash."""
        now = time.time()
        async with self._db.lock:
            try:
                await asyncio.wait_for(
                    self._db.conn.execute(
                        """UPDATE event_delivery
                        SET status = 'pending', started_at = NULL, lease_until = NULL
                        WHERE status = 'processing' AND lease_until IS NOT NULL
                          AND lease_until < ?""",
                        (now,),
                    ),
                    timeout=self._db.operation_timeout,
                )
                await asyncio.wait_for(
                    self._db.conn.execute(
                        """UPDATE event_delivery
                        SET status = 'pending'
                        WHERE status = 'retry_wait' AND available_at <= ?""",
                        (now,),
                    ),
                    timeout=self._db.operation_timeout,
                )
                cursor = await asyncio.wait_for(
                    self._db.conn.execute(
                        "SELECT id, timestamp, type, content FROM event_log"
                    ),
                    timeout=self._db.operation_timeout,
                )
                rows = await cursor.fetchall()
                for row in rows:
                    event_type = EventType(row["type"])
                    tick_type = _tick_type(row["content"], event_type)
                    for spec in routes_for_event(event_type, tick_type):
                        if spec.consumer_id in self._handlers:
                            await self._insert_delivery_locked(
                                str(row["id"]), spec.consumer_id
                            )
                await self._db.conn.commit()
            except BaseException:
                await self._db.conn.rollback()
                raise

    async def list_events(
        self,
        limit: int = 100,
        event_type: EventType | None = None,
        correlation_id: str | None = None,
    ) -> list[Event]:
        clauses: list[str] = []
        params: list[str | int] = []
        if event_type is not None:
            clauses.append("type = ?")
            params.append(event_type.value)
        if correlation_id is not None:
            clauses.append("correlation_id = ?")
            params.append(correlation_id)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        params.append(limit)
        sql = (
            "SELECT id, timestamp, source, type, content, correlation_id "
            f"FROM event_log{where} ORDER BY timestamp DESC, id LIMIT ?"
        )
        async with self._db.lock:
            cursor = await self._db.conn.execute(sql, params)
            rows = await cursor.fetchall()
        return [_row_to_event(row) for row in rows]

    async def _admit(self, event: Event) -> tuple[bool, tuple[str, ...]]:
        event_content = json.dumps(
            event.content, default=str, allow_nan=False, ensure_ascii=False
        )
        specs = self._matching_specs(event)
        consumer_ids = tuple(spec.consumer_id for spec in specs)
        lock_acquired = False
        try:
            await asyncio.wait_for(
                self._db.lock.acquire(), timeout=self._db.lock_timeout
            )
            lock_acquired = True
            insert_cursor = await self._execute_locked(
                """INSERT OR IGNORE INTO event_log
                (id, timestamp, source, type, content, correlation_id)
                VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    event.id,
                    event.timestamp,
                    event.source.value,
                    event.type.value,
                    event_content,
                    event.correlation_id,
                ),
            )
            cursor = await self._execute_locked(
                "SELECT content, source, type, correlation_id "
                "FROM event_log WHERE id = ?",
                (event.id,),
            )
            row = await cursor.fetchone()
            if row is None:
                raise RuntimeError(f"event_log 未写入 event_id={event.id}")
            if (
                row["content"] != event_content
                or row["source"] != event.source.value
                or row["type"] != event.type.value
                or row["correlation_id"] != event.correlation_id
            ):
                raise ValueError(f"event_id 已存在但内容不一致: {event.id}")
            for spec in specs:
                await self._insert_delivery_locked(event.id, spec.consumer_id)
            await asyncio.wait_for(
                self._db.conn.commit(), timeout=self._db.operation_timeout
            )
            inserted = insert_cursor.rowcount == 1
            return inserted, consumer_ids
        except BaseException:
            if lock_acquired:
                await self._db.conn.rollback()
            raise
        finally:
            if lock_acquired:
                self._db.lock.release()

    async def append_in_transaction(self, event: Event) -> tuple[str, ...]:
        """Append an event while the caller owns the shared DB transaction.

        The method deliberately does not acquire the DB lock or commit. Callers
        must hold ``db.lock`` and commit the domain mutation plus this event
        together. Delivery recovery scans the durable rows, so a missed wake
        after commit is harmless.
        """
        event_content = json.dumps(
            event.content, default=str, allow_nan=False, ensure_ascii=False
        )
        specs = self._matching_specs(event)
        cursor = await self._db.conn.execute(
            """INSERT OR IGNORE INTO event_log
            (id, timestamp, source, type, content, correlation_id)
            VALUES (?, ?, ?, ?, ?, ?)""",
            (
                event.id,
                event.timestamp,
                event.source.value,
                event.type.value,
                event_content,
                event.correlation_id,
            ),
        )
        existing = await self._db.conn.execute(
            "SELECT content, source, type, correlation_id "
            "FROM event_log WHERE id = ?",
            (event.id,),
        )
        row = await existing.fetchone()
        if row is None:
            raise RuntimeError(f"event_log 未写入 event_id={event.id}")
        if (
            row["content"] != event_content
            or row["source"] != event.source.value
            or row["type"] != event.type.value
            or row["correlation_id"] != event.correlation_id
        ):
            raise ValueError(f"event_id 已存在但内容不一致: {event.id}")
        for spec in specs:
            await self._insert_delivery_locked(event.id, spec.consumer_id)
        if cursor.rowcount == 1:
            self.persisted_count += 1
        return tuple(spec.consumer_id for spec in specs)

    async def is_durable(self, event_id: str) -> bool:
        """Return whether an event row already exists in the durable log."""
        if self._db.in_transaction:
            cursor = await self._db.conn.execute(
                "SELECT 1 FROM event_log WHERE id = ?", (event_id,)
            )
            return await cursor.fetchone() is not None
        async with self._db.lock:
            cursor = await self._db.conn.execute(
                "SELECT 1 FROM event_log WHERE id = ?", (event_id,)
            )
            return await cursor.fetchone() is not None

    async def announce_committed(self, event: Event) -> None:
        """Broadcast and wake consumers after an external transaction commits."""
        async with self._db.lock:
            cursor = await self._db.conn.execute(
                "SELECT 1 FROM event_log WHERE id = ?", (event.id,)
            )
            row = await cursor.fetchone()
        if row is None:
            raise ValueError(f"event_id 尚未提交: {event.id}")
        consumer_ids = tuple(spec.consumer_id for spec in self._matching_specs(event))
        self._queue.track(len(consumer_ids))
        if consumer_ids:
            self._compat_pending[event.id] = len(consumer_ids)
        self._broadcast(event)
        for consumer_id in consumer_ids:
            self._wake(consumer_id)

    async def try_mark_effect_in_transaction(
        self, event_id: str, consumer_id: str
    ) -> bool:
        """Claim a consumer effect marker while the caller owns the transaction."""
        cursor = await self._db.conn.execute(
            """INSERT OR IGNORE INTO event_effect (event_id, consumer_id, applied_at)
            VALUES (?, ?, ?)""",
            (event_id, consumer_id, time.time()),
        )
        return cursor.rowcount == 1

    async def has_effect(self, event_id: str, consumer_id: str) -> bool:
        """Return whether a consumer effect was already committed."""
        async with self._db.lock:
            cursor = await self._db.conn.execute(
                "SELECT 1 FROM event_effect WHERE event_id = ? AND consumer_id = ?",
                (event_id, consumer_id),
            )
            return await cursor.fetchone() is not None

    async def _execute_locked(
        self, sql: str, parameters: tuple[object, ...] = ()
    ) -> aiosqlite.Cursor:
        return await asyncio.wait_for(
            self._db.conn.execute(sql, parameters),
            timeout=self._db.operation_timeout,
        )

    async def _insert_delivery_locked(self, event_id: str, consumer_id: str) -> None:
        await self._execute_locked(
            """INSERT OR IGNORE INTO event_delivery
            (event_id, consumer_id, status, attempts, available_at)
            VALUES (?, ?, 'pending', 0, 0.0)""",
            (event_id, consumer_id),
        )

    def _matching_specs(self, event: Event) -> tuple[RouteSpec, ...]:
        tick_type = _tick_type(event.content, event.type)
        specs = [
            spec
            for spec in routes_for_event(event.type, tick_type)
            if spec.consumer_id in self._handlers
        ]
        specs.extend(self._legacy_specs.get(event.type, ()))
        return tuple(specs)

    def _start_worker(self, consumer_id: str) -> None:
        task = self._workers.get(consumer_id)
        if task is None or task.done():
            self._workers[consumer_id] = asyncio.create_task(
                self._consumer_worker(consumer_id)
            )

    async def _dispatch_wakes(self) -> None:
        while True:
            consumer_id = await self._wake_queue.get()
            try:
                event = self._wake_events.get(consumer_id)
                if event is not None:
                    event.set()
            finally:
                self._wake_queue.task_done()

    async def _consumer_worker(self, consumer_id: str) -> None:
        wake_event = self._wake_events[consumer_id]
        while not self._stop_event.is_set():
            wake_event.clear()
            try:
                delivery = await self._claim_next(consumer_id)
            except asyncio.CancelledError:
                raise
            except Exception:
                self._logger.exception(
                    "读取 delivery 失败，worker 退避 consumer_id=%s", consumer_id
                )
                try:
                    await asyncio.wait_for(
                        self._stop_event.wait(), timeout=_SCAN_INTERVAL_SECONDS
                    )
                except asyncio.TimeoutError:
                    pass
                continue
            if delivery is not None:
                event, attempts = delivery
                handler = self._handlers[consumer_id]
                if await self._has_effect(event.id, consumer_id):
                    await self._mark_success(consumer_id, event.id)
                    continue
                try:
                    await handler(event)
                except asyncio.CancelledError:
                    raise
                except Exception as error:
                    await self._mark_failure(consumer_id, event.id, attempts, error)
                    self._logger.exception(
                        "handler 处理失败 event_id=%s consumer_id=%s attempts=%d",
                        event.id,
                        consumer_id,
                        attempts,
                    )
                else:
                    await self._mark_success(consumer_id, event.id)
                continue
            try:
                await asyncio.wait_for(
                    wake_event.wait(), timeout=_SCAN_INTERVAL_SECONDS
                )
            except asyncio.TimeoutError:
                pass

    async def _claim_next(
        self, consumer_id: str
    ) -> tuple[Event, int] | None:
        now = time.time()
        async with self._db.lock:
            try:
                await self._db.conn.execute(
                    """UPDATE event_delivery
                    SET status = 'pending'
                    WHERE consumer_id = ? AND status = 'retry_wait'
                      AND available_at <= ?""",
                    (consumer_id, now),
                )
                cursor = await self._db.conn.execute(
                    """SELECT d.event_id, d.attempts,
                              e.id, e.timestamp, e.source, e.type,
                              e.content, e.correlation_id
                       FROM event_delivery AS d
                       JOIN event_log AS e ON e.id = d.event_id
                       WHERE d.consumer_id = ? AND d.status = 'pending'
                         AND d.available_at <= ?
                         AND NOT EXISTS (
                           SELECT 1
                           FROM event_delivery AS prior_d
                           JOIN event_log AS prior_e
                             ON prior_e.id = prior_d.event_id
                           WHERE prior_d.consumer_id = d.consumer_id
                             AND prior_d.status NOT IN ('succeeded', 'dead_letter')
                             AND (
                               prior_e.timestamp < e.timestamp
                               OR (prior_e.timestamp = e.timestamp
                                   AND prior_e.id < e.id)
                             )
                         )
                       ORDER BY e.timestamp, e.id
                       LIMIT 1""",
                    (consumer_id, now),
                )
                row = await cursor.fetchone()
                if row is None:
                    await self._db.conn.commit()
                    return None
                attempts = int(row["attempts"]) + 1
                lease_until = now + _DELIVERY_LEASE_SECONDS
                await self._db.conn.execute(
                    """UPDATE event_delivery
                    SET status = 'processing', attempts = ?, started_at = ?,
                        lease_until = ?, last_error = NULL
                    WHERE event_id = ? AND consumer_id = ? AND status = 'pending'""",
                    (attempts, now, lease_until, row["event_id"], consumer_id),
                )
                await self._db.conn.commit()
                return _row_to_event(row), attempts
            except BaseException:
                await self._db.conn.rollback()
                raise

    async def _mark_success(self, consumer_id: str, event_id: str) -> None:
        while not self._stop_event.is_set():
            async with self._db.lock:
                try:
                    await self._db.conn.execute(
                        """INSERT OR IGNORE INTO event_effect
                        (event_id, consumer_id, applied_at) VALUES (?, ?, ?)""",
                        (event_id, consumer_id, time.time()),
                    )
                    await self._db.conn.execute(
                        """UPDATE event_delivery
                        SET status = 'succeeded', completed_at = ?,
                            lease_until = NULL, last_error = NULL
                        WHERE event_id = ? AND consumer_id = ?
                          AND status = 'processing'""",
                        (time.time(), event_id, consumer_id),
                    )
                    await self._db.conn.commit()
                except asyncio.CancelledError:
                    await self._db.conn.rollback()
                    raise
                except Exception:
                    await self._db.conn.rollback()
                    self._logger.exception(
                        "delivery 成功状态写入失败，等待重试 "
                        "event_id=%s consumer_id=%s",
                        event_id,
                        consumer_id,
                    )
                else:
                    self._compat_delivery_done(event_id)
                    return
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(), timeout=_SCAN_INTERVAL_SECONDS
                )
            except asyncio.TimeoutError:
                pass

    async def _mark_failure(
        self, consumer_id: str, event_id: str, attempts: int, error: Exception
    ) -> None:
        spec = self._specs.get(consumer_id)
        max_attempts = spec.max_attempts if spec is not None else _DELIVERY_MAX_ATTEMPTS
        terminal = attempts >= max_attempts
        status = "dead_letter" if terminal else "retry_wait"
        delay = _DELIVERY_RETRY_DELAYS[
            min(attempts - 1, len(_DELIVERY_RETRY_DELAYS) - 1)
        ]
        available_at = time.time() + delay if not terminal else time.time()
        while not self._stop_event.is_set():
            async with self._db.lock:
                try:
                    await self._db.conn.execute(
                        """UPDATE event_delivery
                        SET status = ?, available_at = ?, lease_until = NULL,
                            last_error = ?
                        WHERE event_id = ? AND consumer_id = ?
                          AND status = 'processing'""",
                        (
                            status,
                            available_at,
                            f"{type(error).__name__}: {error}"[:2000],
                            event_id,
                            consumer_id,
                        ),
                    )
                    await self._db.conn.commit()
                except asyncio.CancelledError:
                    await self._db.conn.rollback()
                    raise
                except Exception:
                    await self._db.conn.rollback()
                    self._logger.exception(
                        "delivery 失败状态写入失败，等待重试 "
                        "event_id=%s consumer_id=%s",
                        event_id,
                        consumer_id,
                    )
                else:
                    if terminal:
                        self._compat_delivery_done(event_id)
                    return
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(), timeout=_SCAN_INTERVAL_SECONDS
                )
            except asyncio.TimeoutError:
                pass

    async def _has_effect(self, event_id: str, consumer_id: str) -> bool:
        async with self._db.lock:
            cursor = await self._db.conn.execute(
                "SELECT 1 FROM event_effect WHERE event_id = ? AND consumer_id = ?",
                (event_id, consumer_id),
            )
            return await cursor.fetchone() is not None

    async def _wait_until_idle(self) -> None:
        while True:
            async with self._db.lock:
                cursor = await self._db.conn.execute(
                    """SELECT COUNT(*)
                    FROM event_delivery
                    WHERE status IN ('pending', 'processing')
                       OR (status = 'retry_wait' AND available_at <= ?)""",
                    (time.time(),),
                )
                row = await cursor.fetchone()
            if row is not None and int(row[0]) == 0:
                return
            await asyncio.sleep(_SCAN_INTERVAL_SECONDS)

    async def _has_no_ready_deliveries(self) -> bool:
        async with self._db.lock:
            cursor = await self._db.conn.execute(
                """SELECT COUNT(*)
                FROM event_delivery
                WHERE status IN ('pending', 'processing')
                   OR (status = 'retry_wait' AND available_at <= ?)""",
                (time.time(),),
            )
            row = await cursor.fetchone()
        return row is None or int(row[0]) == 0

    async def _stop_workers(self, cancel: bool = False) -> None:
        tasks = list(self._workers.values())
        if cancel:
            for task in tasks:
                if not task.done():
                    task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._workers.clear()

    def _wake(self, consumer_id: str) -> None:
        try:
            self._wake_queue.put_nowait(consumer_id)
        except asyncio.QueueFull:
            self._logger.warning(
                "事件总线唤醒队列已满，依赖周期扫描 consumer_id=%s", consumer_id
            )

    def _compat_delivery_done(self, event_id: str) -> None:
        remaining = self._compat_pending.get(event_id)
        if remaining is None:
            return
        if remaining <= 1:
            self._compat_pending.pop(event_id, None)
            self._queue.task_done()
        else:
            self._compat_pending[event_id] = remaining - 1

    def _broadcast(self, event: Event) -> None:
        for sink in self._sse_sinks:
            try:
                sink.put_nowait(event)
            except asyncio.QueueFull:
                sink.get_nowait()
                sink.put_nowait(event)


def _tick_type(content: object, event_type: EventType) -> TickType | None:
    if event_type is not EventType.CLOCK_TICK:
        return None
    value: object
    if isinstance(content, dict):
        content_dict = cast(dict[str, object], content)
        value = content_dict.get("tick_type")
    elif isinstance(content, str):
        try:
            content_dict = cast(dict[str, object], json.loads(content))
        except (TypeError, ValueError, json.JSONDecodeError):
            return None
        value = content_dict.get("tick_type")
    else:
        return None
    if not isinstance(value, str):
        return None
    try:
        return TickType(value)
    except ValueError:
        return None


def _row_to_event(row: aiosqlite.Row) -> Event:
    return Event(
        id=row["id"],
        timestamp=row["timestamp"],
        source=Source(row["source"]),
        type=EventType(row["type"]),
        content=json.loads(row["content"])
        if isinstance(row["content"], str)
        else row["content"],
        correlation_id=row["correlation_id"],
    )
