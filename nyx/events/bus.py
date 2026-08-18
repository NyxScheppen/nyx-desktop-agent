import asyncio
import json
import logging
from collections import defaultdict
from collections.abc import Awaitable, Callable

import aiosqlite

from nyx.db import Database
from nyx.enums import EventType, Source
from nyx.types import Event

Handler = Callable[[Event], Awaitable[None]]


class _EventQueue(asyncio.Queue[Event]):
    """asyncio.Queue + 队首放回：persist 失败重试不丢事件、保序。"""

    def put_left(self, item: Event) -> None:
        """放回队首并计入未完成任务（配合 task_done/join）。

        asyncio.Queue 无公开队首插入，这里触碰其内部 deque 与未完成计数；
        typeshed 不声明这两个私有属性，用 getattr 绕过静态检查。
        """
        getattr(self, "_queue").appendleft(item)
        setattr(self, "_unfinished_tasks", getattr(self, "_unfinished_tasks") + 1)


class EventBus:
    """单一事件管道：publish 入队，run 串行「persist → 内部分发 → SSE 广播」。

    db 由组合根注入（同所有 store 共享），db.lock 串行化同一连接的并发访问。
    """

    def __init__(self, db: Database) -> None:
        self._db = db
        self._logger = logging.getLogger(__name__)
        self._queue: _EventQueue = _EventQueue()
        self._handlers: dict[EventType, list[Handler]] = defaultdict(list)
        self._sse_sinks: list[asyncio.Queue[Event]] = []
        self.persisted_count = 0   # 成功落库计数（监督器据此判定「恢复」）

    def subscribe(self, event_type: EventType, handler: Handler) -> None:
        self._handlers[event_type].append(handler)

    def add_sse_sink(self, sink: asyncio.Queue[Event]) -> None:
        self._sse_sinks.append(sink)

    def remove_sse_sink(self, sink: asyncio.Queue[Event]) -> None:
        if sink in self._sse_sinks:  # 幂等：SSE 断连 cleanup 可能调两次
            self._sse_sinks.remove(sink)

    async def publish(self, event: Event) -> None:
        """入队即返回，无 I/O。持久化/分发/广播由 run() 完成。"""
        await self._queue.put(event)

    async def run(self) -> None:
        """主循环：顺序处理每个事件。由组合根以 task 启动、cancel 停止。"""
        while True:
            event = await self._queue.get()
            try:
                try:
                    await self._persist(event)      # 先落库：即使分发失败，溯源不丢
                except Exception:
                    self._logger.exception(
                        "持久化失败，事件放回队首 event_id=%s", event.id
                    )
                    self._queue.put_left(event)     # 不丢：放回队首，监督器重启后重试
                    raise
                self.persisted_count += 1
                for handler in self._handlers.get(event.type, []):
                    try:
                        await handler(event)
                    except Exception:
                        self._logger.exception(
                            "handler 处理事件失败 event_id=%s type=%s",
                            event.id, event.type.value,
                        )
                self._broadcast(event)
            finally:
                self._queue.task_done()

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

    async def _persist(self, event: Event) -> None:
        async with self._db.lock:
            await self._db.conn.execute(
                "INSERT INTO event_log (id, timestamp, source, type, content, "
                "correlation_id) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    event.id,
                    event.timestamp,
                    event.source.value,
                    event.type.value,
                    json.dumps(event.content),
                    event.correlation_id,
                ),
            )
            await self._db.conn.commit()

    def _broadcast(self, event: Event) -> None:
        for sink in self._sse_sinks:
            try:
                sink.put_nowait(event)
            except asyncio.QueueFull:
                # 慢客户端背压：丢最旧保最新（SSE 允许丢帧，防无界内存）
                sink.get_nowait()
                sink.put_nowait(event)


def _row_to_event(row: aiosqlite.Row) -> Event:
    return Event(
        id=row["id"],
        timestamp=row["timestamp"],
        source=Source(row["source"]),
        type=EventType(row["type"]),
        content=json.loads(row["content"]),
        correlation_id=row["correlation_id"],
    )
