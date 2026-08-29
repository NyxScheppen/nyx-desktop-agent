# 事件总线 + 路由

> 范围：`events/bus.py`（`EventBus`：publish / subscribe / run / list_events + add_sse_sink / remove_sse_sink）、`events/routing.py`（`ROUTING` / `TICK_ROUTING` 纯数据）、`events/event.py`（`internal_event` 内部事件构造 + `SECONDS_PER_DAY`/`SECONDS_PER_HOUR` 时间单位常量，各 Facade 共享）、`event_log` 持久化、`correlation_id` 溯源约定。
> 纯基础设施 spec：总线是通信管道，不含任何 Facade 业务逻辑、不含 API（SSE HTTP 端点归 18-api，本 spec 只提供 sink 机制）。
> spec 只定义契约（总线签名 + 路由映射 + 溯源约定）；实现以 `nyx/events/bus.py` / `nyx/events/routing.py` / `nyx/events/event.py` 源文件为准。

## 元信息

- **前置依赖**：01-types（`Event` / `EventType` / `Source` / `TickType`）、04-db（`Database`（conn+lock）+ `event_log` 表）

## 用户故事

> 作为 Nyx 系统的开发者，我想要一条所有模块通信的单一事件管道——publish 即入队、run 按订阅分发并落库、SSE 广播全部事件、`correlation_id` 贯穿因果链可查——以便六大模块只通过事件解耦、前端能看到全量事件流、任何一次行动都能沿 correlation_id 溯源。

## 验收标准

- [ ] `bus.py` 含 `EventBus`（`__init__(db)` + `publish` / `subscribe` / `run` / `list_events` / `add_sse_sink` / `remove_sse_sink`）（实现见 `nyx/events/bus.py`）
- [ ] `routing.py` 含 `ROUTING`（19 键）+ `TICK_ROUTING`（5 键）（实现见 `nyx/events/routing.py`）
- [ ] `event.py` 含 `internal_event(type_, content, correlation_id) -> Event`（新 `uuid4` id + `time.time` 时间戳 + `Source.INTERNAL`）+ `internal_text_event(type_, content, correlation_id) -> Event`（纯文本 content 包成 `{"content": ...}` 载荷）与 `SECONDS_PER_DAY`/`SECONDS_PER_HOUR` 常量（实现见 `nyx/events/event.py`）
- [ ] `publish()` 只入队（无 I/O、不落库）；`run()` 按「persist → SSE 广播 → 内部分发」顺序处理（先广播：SSE 观察者立刻收到事件本身，不被阻塞 handler 拖慢）
- [ ] 多 handler 按订阅序调用；handler 收到完整 `Event`（含 `correlation_id`，供下游继承）
- [ ] SSE sink 收到**全部**事件（含 `ROUTING` 为空的 `think`/`speak` 等）；`add`/`remove_sse_sink` 生效
- [ ] `list_events()` 按 `event_type` / `correlation_id` 过滤、`limit` 截断；`event_log` 行↔`Event` 往返（`content` JSON、枚举列 `.value`）
- [ ] `correlation_id` 由总线**透传不改写**
- [ ] handler 抛异常：`logger.exception` 记录完整 traceback，继续下一个 handler、不打断 SSE 广播；`_persist` 失败：事件放回队首后仍传播（不丢、保序），同一事件连续 `_PERSIST_MAX_ATTEMPTS` 次失败死信丢弃（`logger.critical` 记录，不熔断）；`_persist` 失败回滚连接不留坏事务
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
- **persist 失败队首放回**：`_persist` 抛异常时 `run()` 先 `put_left(event)` 把事件放回队首再重抛——监督器重启后重试不丢、保 publish 顺序（head-of-line blocking：DB 挂时后续事件排队等待，不乱序）。`persisted_count` 是公开单调计数，每次成功落库 +1，供监督器判定「崩溃前是否健康」（恢复信号）
- **毒丸死信**：同一事件（`event.id` 记入 `_persist_attempts`）persist 连续失败 `_PERSIST_MAX_ATTEMPTS` 次 → `logger.critical` 记录后丢弃（`continue` 跳过分发/广播，不 re-raise、不再放回队首）。避免「一条永远落不了库的事件」无限放回队首 + 监督器重试 → 熔断杀进程——即便 DB 健康，毒丸（不可序列化 content / 重复 id / 中毒连接）也不再拖垮整队。瞬态 DB 故障靠「重试能成功」与毒丸区分，而非靠全局计数
- **`_persist` 失败回滚 + 序列化**：`INSERT + commit` 包 try，`except BaseException` 先 `rollback()` 再 re-raise（对齐 `db.migrate` 的 BEGIN/COMMIT/ROLLBACK；`CancelledError` 是 `BaseException`，关停取消落在 INSERT 与 commit 之间也回滚、不留未提交事务）——瞬态 `commit()` 失败不再把共享连接留在坏事务（否则下次 INSERT 报「transaction within transaction」→ 重试变永久失败）。`json.dumps(..., default=str, allow_nan=False)` 与 SSE 对齐：UUID/datetime/Decimal/Enum 序列化为字符串而非抛 TypeError；NaN/inf 由 `allow_nan=False` 抛 ValueError（`default=str` 拦不住 float，原会写出非法 `NaN`/`Infinity` 字面量、严格 JSON 消费者拒收），被死信路径兜底
- **关停不丢事件**：`run()` 的 persist 失败路径里 `except asyncio.CancelledError` 先 `put_left(event)` 再 re-raise（`CancelledError` 是 `BaseException`，不会进 `except Exception`）——关停时已弹出的事件回到队列，`_unfinished_tasks` 计数一致
- **顺序分发**：`run()` 逐个 `await handler`，不 spawn 并发 task；事件按 publish 顺序处理，下游事件（handler 里再 publish）排在当前事件之后
- **correlation_id 是发布者约定**：总线不生成、不修改 `id` / `timestamp` / `correlation_id`，只透传 + `list_events` 按它过滤。溯源链：根事件（用户消息/时钟/观察）`correlation_id = 自身 id`；下游事件 `correlation_id = 上游 Event.correlation_id`（恒定根——同一因果链的事件共享同一 correlation_id）；前端按 `correlation_id` 分组、沿 `timestamp` 排序溯源
- **共享连接并发安全**：04-db 的 `connect()` 返回 `Database(conn, lock)`，组合根（18-api）把它注入所有 store；store 的 DB 读写都 `async with self._db.lock:` 串行化（同一 `aiosqlite.Connection` 不能并发 execute/commit）。`EventBus.__init__(db)` 即此约定首个落地处
- **event_log 行↔Event 序列化归总线**：05-event 拥有 `event_log`（04-db 表归属表已定），`_row_to_event` / `_persist` 内联在源文件
- **`internal_event` + 时间单位常量抽出共享**：desire/memory/inner_life/activity 四个 Facade 都发布内部事件、都算时间衰减/恢复，原 `_internal_event`（uuid4/time.time/Source.INTERNAL）与 `_SECONDS_PER_DAY` 三处复制。抽到 `events/event.py` 单一模块，Event 结构或时间戳语义一变只改这一处（反冗余）
- **ROUTING / TICK_ROUTING 映射（纯数据，跨 spec 契约）**：总线运行时靠 `subscribe`，不读此表；但它定义「事件 → 内部消费者」的单一契约，供一致性测试与其它 spec 引用。非空路由如下，其余（`DESIRE_EXPIRED` / `ACTIVITY_START` / `ACTIVITY_INTERRUPTED` / `SPEAK` / `ASK` / `THINK` / `MUTTER` / `INITIATE_CHAT` / `EMOTION_UPDATE` / `REFLECTION_DONE` / `MEMORY_CREATED` / `MEMORY_PROMOTED`）无内部消费者、仅广播前端：

  | 事件 | 内部消费者 |
  |---|---|
  | `USER_MESSAGE` | `expression` |
  | `USER_MATERIAL` | `activity` |
  | `OBSERVATION_STATE` | `inner_life`, `desire` |
  | `DESIRE_GENERATED` | `activity` |
  | `DESIRE_SATISFIED` | `inner_life` |
  | `ACTIVITY_END` | `desire`, `inner_life`, `memory` |
  | `REFLECTION` | `inner_life` |

  `TICK_ROUTING`（`clock_tick` 按 `content.tick_type` 二次路由，1 tick_type → 1 消费者）：`SCHEDULE_BLOCK_START`→`activity`、`DESIRE_EVAL`→`desire`、`MUTTER_CHECK`→`expression`、`INITIATE_CHAT_CHECK`→`expression`、`REFLECTION_CHECK`→`inner_life`。完整映射以 `nyx/events/routing.py` 为准。

## 测试要点

- [ ] 单元测试 `tests/test_event/`：
  - [ ] **事件构造原语**（`test_event.py`，无 DB）：`internal_event(EventType.MUTTER, {}, "c")` → `Event` 的 `source is Source.INTERNAL`、`type`/`content`/`correlation_id` 原样、`id` 非空 str、`timestamp` 是 float；`internal_text_event(EventType.THINK, "hi", "c")` → `content == {"content": "hi"}`；`SECONDS_PER_DAY == 86400.0`、`SECONDS_PER_HOUR == 3600.0`
  - [ ] **纯数据**（`test_routing.py`，不实例化总线）：`set(ROUTING.keys()) == set(EventType) - {EventType.CLOCK_TICK}`（19 键）；`set(TICK_ROUTING.keys()) == set(TickType)`（5 键）；所有值的模块名 ⊆ `{"expression", "inner_life", "desire", "activity"}`——注意模块名含 `memory`（`ACTIVITY_END`→`memory`），此集合随 routing.py 更新
  - [ ] **总线机制**（`test_bus.py`，`db = await connect(":memory:")`——内部已设 `row_factory=aiosqlite.Row` + 跑迁移，直接返回 `Database`；`list_events` 的 `_row_to_event` 按列名 `row["id"]` 取值，缺 row_factory 会 TypeError）+ fake async handler + 真 `asyncio.Queue` sink）：
    - [ ] `publish` 只入队：publish 后 handler 未调用、`list_events()` 无记录（未到 run）
    - [ ] `run()` 作 task 跑：publish 事件 → `event_log` 落库 + handler 收到完整 `Event`（`id`/`timestamp`/`source`/`type`/`content`/`correlation_id` 全原样）+ SSE sink 收到同一 `Event`
    - [ ] 多 handler 按订阅序调用（fake 记录调用顺序断言）
    - [ ] `list_events` 过滤：`event_type=` / `correlation_id=` / `limit=` / 默认（按 `timestamp DESC`）
    - [ ] 行↔`Event` 往返：`content` 是 `json.loads` 后的 dict、`source`/`type` 从 `.value` 转回枚举成员
    - [ ] `add_sse_sink` 后收到、`remove_sse_sink` 后不再收到
    - [ ] sink 满（`Queue(maxsize=N)`）→ `_broadcast` 丢最旧保最新（不抛 `QueueFull`、`run()` 不死）
    - [ ] `correlation_id` 透传：publish 什么值，落库 + handler + sink 里就是什么值（总线不改写）
    - [ ] handler 抛异常 → `logger.exception` 记录（`caplog` 断言含 traceback）、后续 handler 照跑、SSE 照广播、`run()` 任务不死；`task_done()` 照走（`queue.join()` 不挂）
    - [ ] `_persist` 抛异常（monkeypatch `_persist` 为 raise）→ 传播、`run()` 任务终止、事件放回队首不丢（`_queue.qsize()` 仍为 1）；`_persist` 失败一次后成功 → 事件重试落库 + handler 收到（不丢）
    - [ ] 毒丸死信：`_persist` 恒 raise → 手动驱动 3 轮 run()，第 3 轮死信丢弃（`_queue.qsize()` 为 0、`run()` 任务不死）、`caplog` 含「死信丢弃」+ event.id
    - [ ] 回滚：monkeypatch `conn.commit` 抛 `aiosqlite.Error`、spy `conn.rollback` → `_persist` 抛 `aiosqlite.Error` 且 rollback 被调
    - [ ] 序列化：content 含 `uuid.uuid4()` → 落库往返后该值为字符串（`default=str`，不抛 TypeError）；content 含 `float("nan")` → `_persist` 抛 `ValueError`（`allow_nan=False`，不写出非法 `NaN` 字面量）
    - [ ] `put_left` 补齐副作用：`put_left` 后 `wait_for(join(), timeout=0.05)` 抛 `TimeoutError`（`_finished` 被 clear，`join()` 语义正确）
- [ ] 集成测试：无（总线是基础设施，无 Facade 管道；handler 的真实绑定归 18-api 组合根）
- [ ] E2E 测试：无

## 完成定义

- [ ] `ruff check` 零报错
- [ ] `pyright` 零报错
- [ ] `pytest` 全绿
- [ ] `test-inventory.md` 已更新
- [ ] 六大模块只通过本总线通信（不直接 Facade 互调成环）；`ROUTING` 与 18-api 的组合根订阅一致；前端 SSE 能看到全量事件、`GET /api/events/log` 能按 `correlation_id` 溯源
