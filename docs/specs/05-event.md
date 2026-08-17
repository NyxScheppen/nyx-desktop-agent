# 事件总线 + 路由

> 范围：`events/bus.py`（`EventBus`：publish / subscribe / run / list_events + add_sse_sink / remove_sse_sink）、`events/routing.py`（`ROUTING` / `TICK_ROUTING` 纯数据）、`events/event.py`（`internal_event` 内部事件构造 + `SECONDS_PER_DAY`/`SECONDS_PER_HOUR` 时间单位常量，各 Facade 共享）、`event_log` 持久化、`correlation_id` 溯源约定。
> 纯基础设施 spec：总线是通信管道，不含任何 Facade 业务逻辑、不含 API（SSE HTTP 端点归 18-api，本 spec 只提供 sink 机制）。
> **本文件自包含**：`ROUTING` / `TICK_ROUTING` 与 `bus.py` 完整代码内联在下文，实现不依赖 tech-ref §5 之外的描述。

## 元信息

- **前置依赖**：01-types（`Event` / `EventType` / `Source` / `TickType`）、04-db（`Database`（conn+lock）+ `event_log` 表）

## 用户故事

> 作为 Nyx 系统的开发者，我想要一条所有模块通信的单一事件管道——publish 即入队、run 按订阅分发并落库、SSE 广播全部事件、`correlation_id` 贯穿因果链可查——以便六大模块只通过事件解耦、前端能看到全量事件流、任何一次行动都能沿 correlation_id 溯源。

## 验收标准

- [ ] `bus.py` 含 `EventBus`（`__init__(db)` + `publish` / `subscribe` / `run` / `list_events` / `add_sse_sink` / `remove_sse_sink`），与「`events/bus.py`（完整）」段代码逐字一致
- [ ] `routing.py` 含 `ROUTING`（17 键）+ `TICK_ROUTING`（4 键），与「`events/routing.py`（完整）」段代码逐字一致
- [ ] `event.py` 含 `internal_event(type_, content, correlation_id) -> Event`（新 `uuid4` id + `time.time` 时间戳 + `Source.INTERNAL`）与 `SECONDS_PER_DAY`/`SECONDS_PER_HOUR` 常量，与「`events/event.py`（完整）」段代码逐字一致
- [ ] `publish()` 只入队（无 I/O、不落库）；`run()` 按「persist → 内部分发 → SSE 广播」顺序处理
- [ ] 多 handler 按订阅序调用；handler 收到完整 `Event`（含 `correlation_id`，供下游继承）
- [ ] SSE sink 收到**全部**事件（含 `ROUTING` 为空的 `think`/`speak` 等）；`add`/`remove_sse_sink` 生效
- [ ] `list_events()` 按 `event_type` / `correlation_id` 过滤、`limit` 截断；`event_log` 行↔`Event` 往返（`content` JSON、枚举列 `.value`）
- [ ] `correlation_id` 由总线**透传不改写**
- [ ] handler 抛异常：`logger.exception` 记录完整 traceback，继续下一个 handler、不打断 SSE 广播；`_persist` 失败仍传播
- [ ] 共享连接并发安全：所有 `event_log` 读写都在 `async with self._db.lock` 内（同一连接不并发 execute/commit）
- [ ] `pyright` strict 零报错

## 技术方案

- **新文件**：`nyx/events/bus.py`、`nyx/events/routing.py`（无 Facade、无 API、无业务逻辑）
- **库**：无新库（标准库 `asyncio` / `json` / `collections`；`aiosqlite` 已由 04-db 引入）
- **公开面**：`from nyx.events.bus import EventBus`；`from nyx.events.routing import ROUTING, TICK_ROUTING`（不加 `__all__`）
- **调度模型**：运行时由 `subscribe(event_type, handler)` 建 `_handlers`；`ROUTING` / `TICK_ROUTING` 是**纯数据**（模块名字符串），只用于文档 + 一致性测试，总线**不读**它们
- **SSE 归属**：总线持 sink 列表（`asyncio.Queue[Event]`）+ `add_sse_sink` / `remove_sse_sink`；18-api 只建/拆 HTTP SSE 连接、把 sink 队列挂到总线、从队列读事件并按 §4 payload 格式化
- **SSE 广播全部事件**：`ROUTING` 决定**内部消费者**；SSE 广播与 `ROUTING` 无关，全量推送（`think` 这类「仅日志，不路由」的也推）
- **clock_tick 二次路由**：总线对 `CLOCK_TICK` 不特殊处理——统一 fan-out，各 handler 自己按 `event.content["tick_type"]` 过滤（`TICK_ROUTING` 是文档 + 测试，非运行时源）
- **persist 在 run() 而非 publish()**：`publish` 只入队（Facade 发布不被 DB 写阻塞）；`run()` 里「先 persist 再分发」——即使某 handler 抛异常，事件已落 `event_log`，溯源不丢
- **handler 异常隔离**：Facade 各自兜底自己的错误（03-llm「异常原样上抛由调用方处理」）；总线是最后一道隔离——单个 handler 抛异常时 `logger.exception(...)` 记录完整 traceback 后继续下一个 handler（不波及其它 handler、不打断 SSE 广播）。`_persist` 失败仍传播（数据库是地基，坏了继续跑无意义）。这处 `except Exception` 是唯一合理的宽捕获边界（总线无法枚举 handler 会抛什么：LLM 错 / `aiosqlite.Error` / `JSONDecodeError` / `ValueError`），不是「裸捕获」（静默吞错）
- **顺序分发**：`run()` 逐个 `await handler`，不 spawn 并发 task；事件按 publish 顺序处理，下游事件（handler 里再 publish）排在当前事件之后
- **correlation_id 是发布者约定**：总线不生成、不修改 `id` / `timestamp` / `correlation_id`，只透传 + `list_events` 按它过滤。溯源链：根事件（用户消息/时钟/观察）`correlation_id = 自身 id`；下游事件 `correlation_id = 上游 Event.correlation_id`（恒定根——同一因果链的事件共享同一 correlation_id）；前端按 `correlation_id` 分组、沿 `timestamp` 排序溯源
- **共享连接并发安全**：04-db 的 `connect()` 返回 `Database(conn, lock)`，组合根（18-api）把它注入所有 store；store 的 DB 读写都 `async with self._db.lock:` 串行化（同一 `aiosqlite.Connection` 不能并发 execute/commit）。`EventBus.__init__(db)` 即此约定首个落地处
- **event_log 行↔Event 序列化归总线**：05-event 拥有 `event_log`（04-db 表归属表已定），`_row_to_event` / `_persist` 内联在本文件
- **`internal_event` + 时间单位常量抽出共享**：desire/memory/inner_life/activity 四个 Facade 都发布内部事件、都算时间衰减/恢复，原 `_internal_event`（uuid4/time.time/Source.INTERNAL）与 `_SECONDS_PER_DAY` 三处复制。抽到 `events/event.py` 单一模块，Event 结构或时间戳语义一变只改这一处（反冗余）

### `events/event.py`（完整）

```python
"""事件构造 + 时间单位常量（跨模块共享原语）。

内部事件构造与时间单位常量在 desire/memory/inner_life/activity 四个 Facade
重复，抽出到此统一维护——Event 结构或时间戳语义一变，只改这一处。
"""
import time
from typing import Any
from uuid import uuid4

from nyx.enums import EventType, Source
from nyx.types import Event

SECONDS_PER_DAY = 86400.0
SECONDS_PER_HOUR = 3600.0


def internal_event(
    type_: EventType, content: dict[str, Any], correlation_id: str
) -> Event:
    """构造内部事件：新 uuid4 + 当前时间戳 + Source.INTERNAL。"""
    return Event(
        id=str(uuid4()),
        timestamp=time.time(),
        source=Source.INTERNAL,
        type=type_,
        content=content,
        correlation_id=correlation_id,
    )
```

### `events/routing.py`（完整）

```python
from nyx.enums import EventType, TickType

# ROUTING：EventType → 内部消费者模块名（空 = 仅广播前端，无内部消费者）。
# 值取 {"expression", "inner_life", "desire", "activity"}。
# CLOCK_TICK 不在此表（走 TICK_ROUTING）。
ROUTING: dict[EventType, list[str]] = {
    EventType.USER_MESSAGE:        ["expression"],
    EventType.OBSERVATION_STATE:   ["inner_life", "desire"],   # 情感 + 互动欲加压
    EventType.DESIRE_GENERATED:    ["activity"],
    # 欲望→内在生命唯一耦合点（走事件防成环）
    EventType.DESIRE_SATISFIED:    ["inner_life"],
    EventType.DESIRE_EXPIRED:      [],
    EventType.ACTIVITY_START:      [],
    EventType.ACTIVITY_END:        ["desire", "inner_life"],  # 满足+情感
    EventType.ACTIVITY_INTERRUPTED: [],
    EventType.SPEAK:               [],
    EventType.ASK:                 [],
    EventType.THINK:               [],
    EventType.MUTTER:              [],
    EventType.INITIATE_CHAT:       [],
    EventType.EMOTION_UPDATE:      [],
    # 协调器，内部调 memory/desire
    EventType.REFLECTION:          ["inner_life"],
    EventType.MEMORY_CREATED:      [],
    EventType.MEMORY_PROMOTED:     [],
}

# TICK_ROUTING：clock_tick 按 content.tick_type 二次路由
# （1 个 tick_type → 1 个消费者，非广播）。
TICK_ROUTING: dict[TickType, list[str]] = {
    TickType.SCHEDULE_BLOCK_START: ["activity"],
    TickType.DESIRE_EVAL:          ["desire"],
    TickType.MUTTER_CHECK:         ["expression"],
    TickType.INITIATE_CHAT_CHECK:  ["expression"],
}
```

### `events/bus.py`（完整）

```python
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


class EventBus:
    """单一事件管道：publish 入队，run 串行「persist → 内部分发 → SSE 广播」。

    db 由组合根注入（同所有 store 共享），db.lock 串行化同一连接的并发访问。
    """

    def __init__(self, db: Database) -> None:
        self._db = db
        self._logger = logging.getLogger(__name__)
        self._queue: asyncio.Queue[Event] = asyncio.Queue()
        self._handlers: dict[EventType, list[Handler]] = defaultdict(list)
        self._sse_sinks: list[asyncio.Queue[Event]] = []

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
                await self._persist(event)          # 先落库：即使分发失败，溯源不丢
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
```

## 测试要点

- [ ] 单元测试 `tests/test_event/`：
  - [ ] **事件构造原语**（`test_event.py`，无 DB）：`internal_event(EventType.MUTTER, {}, "c")` → `Event` 的 `source is Source.INTERNAL`、`type`/`content`/`correlation_id` 原样、`id` 非空 str、`timestamp` 是 float；`SECONDS_PER_DAY == 86400.0`、`SECONDS_PER_HOUR == 3600.0`
  - [ ] **纯数据**（`test_routing.py`，不实例化总线）：`set(ROUTING.keys()) == set(EventType) - {EventType.CLOCK_TICK}`（17 键）；`set(TICK_ROUTING.keys()) == set(TickType)`（4 键）；所有值的模块名 ⊆ `{"expression", "inner_life", "desire", "activity"}`
  - [ ] **总线机制**（`test_bus.py`，`db = await connect(":memory:")`——内部已设 `row_factory=aiosqlite.Row` + 跑迁移，直接返回 `Database`；`list_events` 的 `_row_to_event` 按列名 `row["id"]` 取值，缺 row_factory 会 TypeError）+ fake async handler + 真 `asyncio.Queue` sink）：
    - [ ] `publish` 只入队：publish 后 handler 未调用、`list_events()` 无记录（未到 run）
    - [ ] `run()` 作 task 跑：publish 事件 → `event_log` 落库 + handler 收到完整 `Event`（`id`/`timestamp`/`source`/`type`/`content`/`correlation_id` 全原样）+ SSE sink 收到同一 `Event`
    - [ ] 多 handler 按订阅序调用（fake 记录调用顺序断言）
    - [ ] `list_events` 过滤：`event_type=` / `correlation_id=` / `limit=` / 默认（按 `timestamp DESC`）
    - [ ] 行↔`Event` 往返：`content` 是 `json.loads` 后的 dict、`source`/`type` 从 `.value` 转回枚举成员
    - [ ] `add_sse_sink` 后收到、`remove_sse_sink` 后不再收到
    - [ ] `correlation_id` 透传：publish 什么值，落库 + handler + sink 里就是什么值（总线不改写）
    - [ ] handler 抛异常 → `logger.exception` 记录（`caplog` 断言含 traceback）、后续 handler 照跑、SSE 照广播、`run()` 任务不死；`task_done()` 照走（`queue.join()` 不挂）
    - [ ] `_persist` 抛异常（monkeypatch `_persist` 为 raise）→ 传播、`run()` 任务终止；`task_done()` 照走（`queue.join()` 不挂）
- [ ] 集成测试：无（总线是基础设施，无 Facade 管道；handler 的真实绑定归 18-api 组合根）
- [ ] E2E 测试：无

## 完成定义

- [ ] `ruff check` 零报错
- [ ] `pyright` 零报错
- [ ] `pytest` 全绿
- [ ] `test-inventory.md` 已更新
- [ ] 六大模块只通过本总线通信（不直接 Facade 互调成环）；`ROUTING` 与 18-api 的组合根订阅一致；前端 SSE 能看到全量事件、`GET /api/events/log` 能按 `correlation_id` 溯源
