# 模块与事件总线系统

> 范围：`nyx/events/`、`nyx/subscriptions.py`、`nyx/runtime.py`、`nyx/app_context.py`、`nyx/main.py`、`nyx/db.py` 中与事件持久化、消费者投递、路由注册、DB 生命周期、关停生命周期相关的基础设施。  
> 本文件是底层模块总线系统的唯一完整契约，统一覆盖 SQLite 生命周期、事件总线、路由订阅、组合根、REST/SSE、恢复重试和关停。
> 快速事实摘要见 `docs/facts/module-bus-system-facts.md`。
> 本文件只定义运行时基础设施和模块通信边界，不定义共享类型、配置字段、LLM 调用细节或业务模块内部规则。

## 元信息

- **前置依赖**：01-types（`Event` / `EventType` / `Source` / `TickType`）。组合根装配时使用 02-config 和 03-llm；本文件不依赖任何业务系统 spec。
- **实现文件**：`nyx/db.py`、`nyx/events/bus.py`、`nyx/events/event.py`、`nyx/events/routing.py`、`nyx/subscriptions.py`、`nyx/runtime.py`、`nyx/app_context.py`、`nyx/api/routes.py`、`nyx/main.py`。
- **测试文件**：`tests/test_event/`、`tests/test_api/test_subscription.py`、`tests/test_api/test_tick_loop.py`、`tests/test_api/test_context.py`、必要时补 `tests/test_db/`。

## 用户故事

> 作为 Nyx 系统维护者，我想把“事件发生”和“模块消费完成”分成两个可查询、可恢复的事实，并让路由、失败重放、数据库故障、队列背压和关停生命周期拥有统一语义，以便跨模块副作用不会在数据库失败、handler 异常、慢消费者、突发流量或进程退出时静默丢失。

## 验收标准

- [ ] 事件接受和消费者完成分离：`event_log` 记录事件事实，`event_delivery` 记录 `(event_id, consumer_id)` 消费事实。
- [ ] `EventBus.publish(event)` 变为 durable admission：事件和初始 delivery 同事务提交成功后才返回；失败时抛出受理错误，不返回假成功。
- [ ] handler 失败不再只打日志：失败消费者进入 `retry_wait`，按固定退避重试，超过上限进入 `dead_letter`。
- [ ] 反思类 handler 的外部调用必须在本地事务外完成；解析失败或本地提交失败都不得写入成功 effect，delivery 必须可重试。
- [ ] 重放只针对失败消费者；成功消费者不会因其它消费者失败而重复执行。
- [ ] 每个稳定 `consumer_id` 有独立 FIFO worker；同一消费者内按事件顺序执行，不同消费者之间不互相阻塞。
- [ ] 至少一次投递配套幂等：跨模块写副作用按 `(event_id, consumer_id)` 防重。
- [ ] 进程启动会恢复过期 `processing`、到期 `retry_wait` 和缺失 delivery 的已落库事件。
- [ ] 内存队列有界且只作为唤醒优化；队列满不丢失已经持久化的业务事件。
- [ ] 数据库锁等待和 SQL/commit 有超时；连续失败进入熔断，熔断期间新写请求快速失败。
- [ ] 关停按 `RUNNING -> QUIESCING -> DRAINING -> CLOSED` 执行；drain 超时保留未完成 delivery，下次启动恢复，不做全局回滚。
- [ ] 路由声明和运行时订阅由同一份 `RouteSpec` 派生；启动时校验声明、handler、delivery consumer 一致。
- [ ] 组合根导入环被拆除：`app_context` 只装配，不导入 `subscriptions`；`subscriptions` 不导入 `main`；runtime handler 编排在 `runtime.py` 或等价运行期模块。
- [ ] `Database` 提供幂等 `close()`；组合根构造失败和应用退出都会关闭连接。
- [ ] 文档同步：本文件、`tech-reference`、`docs/facts/module-bus-system-facts.md`、`test-inventory.md` 与实现一致。

## 核心决策

### 一致性模型

Nyx 不追求跨模块全局事务，采用：

```text
模块本地事务 + 事务事件记录 + 持久化消费者投递 + 至少一次重放
```

原因：

- handler 可能调用 LLM、发 SSE、写多个模块表、发布后续事件；
- SQLite rollback 只能撤销未提交事务，不能撤销已经发生的 LLM 调用、SSE 广播或其它已提交副作用；
- 因此“全局回滚”会制造伪安全感。

系统承诺：

- 单模块内部状态变更必须原子；
- 模块之间是可恢复的最终一致；
- 失败必须可查询、可重试或进入 dead-letter；
- 不能静默丢失消费者失败。

### 事件的两个事实

`event_log` 表示事件已被接受：

```text
id
timestamp
source
type
content
correlation_id
```

`event_delivery` 表示消费者完成状态：

```text
event_id
consumer_id
status
attempts
available_at
started_at
completed_at
lease_until
last_error
```

唯一键：

```text
(event_id, consumer_id)
```

`event_delivery.status` 域：

| 状态 | 含义 |
|---|---|
| `pending` | 可被 worker 领取 |
| `processing` | 已被 worker 领取，租约未过期 |
| `retry_wait` | handler 失败，等待下次重试 |
| `succeeded` | 该消费者已完成 |
| `dead_letter` | 达到上限或不可重试，需人工/后续工具查看 |

### 投递与重试

默认常量：

```text
_DELIVERY_MAX_ATTEMPTS = 5
_DELIVERY_RETRY_DELAYS = (1.0, 2.0, 4.0, 8.0, 30.0)
_DELIVERY_LEASE_SECONDS = 300.0
_DRAIN_TIMEOUT_SECONDS = 15.0
_ADMISSION_TIMEOUT_SECONDS = 3.0
_WAKE_QUEUE_SIZE = 1024
```

失败流转：

```text
pending
  -> processing
  -> succeeded

processing
  -> retry_wait       # 可重试失败
  -> dead_letter      # 达到重试上限或明确不可重试

retry_wait
  -> pending          # available_at 到期
```

进程崩溃恢复：

- 启动时把 `processing` 且 `lease_until < now` 的记录恢复为 `pending`；
- 启动时把 `retry_wait` 且 `available_at <= now` 的记录恢复为 `pending`；
- 启动时扫描已有 `event_log`，为缺失的路由消费者补齐 delivery，补齐操作必须幂等。

### 幂等

at-least-once 意味着 handler 可能被调用多次。

跨模块写副作用必须满足：

```text
同一个本地事务：
  1. 检查 (event_id, consumer_id) 是否已经应用
  2. 未应用才修改模块状态
  3. 写入已应用标记
  4. commit
```

实现可选择：

- 为每个模块增加 inbox/effect marker；
- 或在 `event_delivery` 上记录足以证明该消费者业务副作用已经提交的字段；
- 但不能只依赖“worker 准备把 delivery 标记为 succeeded”，因为业务 commit 与 delivery commit 之间仍可能崩溃。

LLM 或文件写入这类不可回滚副作用应按 `event_id` 持久化结果；重放时先复用已有结果，不重复生成。

`EventBus.has_effect(event_id, consumer_id) -> bool` 提供提交前的只读检查，供包含外部调用的消费者避免在已完成投递上重复执行外部工作；最终幂等仍必须由事务内的 `try_mark_effect_in_transaction` 保证。

### 事务事件记录

模块状态变更和它产生的后续事件不能跨两个独立提交。

禁止模式：

```text
store.commit()
await bus.publish(event)
```

目标模式：

```text
同一个 db.lock + transaction：
  修改模块表
  写入 event_log
  标记 event_log 等待 delivery expand
  commit
```

`EventBus.publish(event)` 用于根事件和无需绑定其它模块状态事务的事件。对“状态变更 + 后续事件”的生产者，EventBus 必须提供一个可在当前事务内写入事件行的内部 API，或由明确的事务 outbox 机制完成。该 API 不提交事务，由调用方事务统一 commit。

delivery expander 扫描已提交但尚未完全展开的事件，并按 `RouteSpec` 幂等创建 delivery。

### 数据库故障与熔断

数据库基础设施必须避免无限等待：

- 获取 `Database.lock` 有超时；
- SQL/commit 有超时；
- 失败后 rollback；
- 连续失败进入熔断；
- 熔断期间新写请求快速失败；
- 冷却后进入 half-open 探测，成功后恢复。

建议状态：

```text
closed      # 正常
open        # 熔断，快速失败
half_open   # 允许少量探测
```

受理失败映射：

- API 根事件：返回 503 或 429；
- 内部事件：保留调用方事务失败，让活动/记忆/欲望状态按本地恢复机制处理；
- delivery worker：记录失败并退避，不忙等。

### 队列背压

内存队列不是事实来源：

```text
event_log / event_delivery = 事实来源
asyncio.Queue              = 唤醒提示
```

业务事件先持久化，再尝试写入有界唤醒队列。唤醒队列满时：

- 不丢已持久化事件；
- 不再追加重复 wake；
- dispatcher 定时扫描 pending delivery；
- 只把队列满记录为指标/日志。

事件分类：

| 事件 | 背压策略 |
|---|---|
| `USER_MESSAGE` | 不丢；受理成功才返回 event_id；失败返回 503/429 |
| `ACTIVITY_END` / `ACTIVITY_INTERRUPTED` | 不丢；必须持久化和重放 |
| `DESIRE_SATISFIED` / `REFLECTION` | 不丢；必须持久化和重放 |
| `OBSERVATION_STATE` | 默认不丢；只有另立契约为快照语义后才能合并 |
| `CLOCK_TICK` | 可按 `tick_type` 合并；旧 tick 没有逐条业务价值 |
| SSE | 每连接有界；允许丢旧帧、保最新 |

### Drain 关停

生命周期：

```text
RUNNING
  -> QUIESCING
  -> DRAINING
  -> CLOSED
```

`QUIESCING`：

- 停止 HTTP 新写请求；
- 停止 tick / vision 新根事件；
- 允许只读请求继续或由实现决定快速关闭；
- 已在执行的 handler 可以继续产生内部事件。

`DRAINING`：

- 等待 wake queue 排空；
- 等待所有消费者当前任务完成；
- 等待已到期 delivery 进入 `succeeded` 或 `dead_letter`；
- 不等待未来很久才到 `available_at` 的 retry，只要求它已持久化。

超时：

- 取消 worker；
- 未完成 `processing` 依赖 lease 恢复；
- `pending` / `retry_wait` 保留；
- 下次启动恢复；
- 不做全局 rollback。

### 路由与订阅

新增不可变路由规格：

```python
@dataclass(frozen=True)
class RouteSpec:
    event_type: EventType
    consumer_id: str
    module: str
    handler_key: str
    tick_type: TickType | None = None
    max_attempts: int = _DELIVERY_MAX_ATTEMPTS
```

`RouteSpec` 是唯一来源，用来派生：

- `ROUTING`；
- `TICK_ROUTING`；
- delivery 创建；
- runtime 订阅；
- 启动一致性校验；
- 路由测试。

稳定 `consumer_id` 示例：

```text
expression.user_message
inner_life.observation_state
desire.observation_state
activity.desire_generated
inner_life.desire_satisfied
desire.activity_end
inner_life.activity_end
memory.activity_end
inner_life.reflection
activity.schedule_block_start
desire.desire_eval
expression.mutter_check
expression.initiate_chat_check
inner_life.reflection_check
```

订阅 API：

```python
def subscribe(spec: RouteSpec, handler: Handler) -> SubscriptionToken
def unsubscribe(token: SubscriptionToken) -> None
```

规则：

- 同一 `consumer_id` 重复注册相同 handler：幂等 no-op；
- 同一 `consumer_id` 注册不同 handler：抛错；
- 不允许 lambda 作为无法识别的消费者身份；
- 启动时声明消费者与实际 handler 不一致则 fail fast。

### Tick 路由

`CLOCK_TICK` 仍是事件类型，但 `tick_type` 必须参与路由规格：

```text
CLOCK_TICK + SCHEDULE_BLOCK_START -> activity.schedule_block_start
CLOCK_TICK + DESIRE_EVAL          -> desire.desire_eval
CLOCK_TICK + MUTTER_CHECK         -> expression.mutter_check
CLOCK_TICK + INITIATE_CHAT_CHECK  -> expression.initiate_chat_check
CLOCK_TICK + REFLECTION_CHECK     -> inner_life.reflection_check
```

不再把全部 tick 分发隐藏在一个大 `if/elif` handler 中。

### 模块边界

允许直接 Facade 查询：

- 当前状态；
- 待处理欲望；
- 记忆检索；
- 当前活动；
- 运行期观察快照。

必须走事件的跨模块写副作用：

- 活动结束导致 desire / inner_life / memory 更新；
- 欲望生成导致 activity 消费；
- 反思请求导致 inner_life 变更慢变量；
- 用户消息导致 expression 回复、打断活动等组合根编排。

事件 payload 必须包含消费者完成工作所需事实；消费者不能依赖另一个消费者的执行顺序。

### 组合根与导入边界

目标边界：

- `app_context.py` 只负责构造 DB、stores、facades、runtime state；
- `app_context.py` 不调用 `subscribe(app)`；
- `subscriptions.py` 不导入 `main.py`；
- `_on_user_message`、tick 分发、关停编排移动到 `runtime.py` 或等价运行期模块；
- `main.py` 只做 load config、build context、bind routes、build FastAPI、start lifecycle；
- `api.routes` 只依赖可测试的 app/context 类型，不参与订阅；
- `_App` 只作为组合根内部对象，不传入 Facade。

现有 `state_holder[0]` / `reflect_holder[0]` / `observation_holder[0]` 可变列表占位应替换为明确绑定对象或可检查闭包；未绑定时抛出带上下文的 `RuntimeError`。

## 数据变更

### `event_delivery`

```sql
CREATE TABLE event_delivery (
    event_id TEXT NOT NULL,
    consumer_id TEXT NOT NULL,
    status TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    available_at REAL NOT NULL DEFAULT 0.0,
    started_at REAL,
    completed_at REAL,
    lease_until REAL,
    last_error TEXT,
    PRIMARY KEY (event_id, consumer_id),
    FOREIGN KEY (event_id) REFERENCES event_log(id)
);
```

索引：

```sql
CREATE INDEX idx_event_delivery_ready
ON event_delivery(status, available_at);

CREATE INDEX idx_event_delivery_consumer_ready
ON event_delivery(consumer_id, status, available_at);
```

### `event_effect`

需要支持消费者业务幂等，推荐表：

```sql
CREATE TABLE event_effect (
    event_id TEXT NOT NULL,
    consumer_id TEXT NOT NULL,
    applied_at REAL NOT NULL,
    PRIMARY KEY (event_id, consumer_id),
    FOREIGN KEY (event_id) REFERENCES event_log(id)
);
```

若实现能证明 `event_delivery` 自身可作为业务幂等 marker，并且模块状态变更与 marker 写入在同一事务中完成，可以不建独立 `event_effect`；否则必须建。

### 欲望系统辅助表

`long_term_desire.name_normalized` 是由欲望系统按 `strip + casefold + 连续空白折叠`
生成的持久化键；数据库必须提供唯一索引，作为并发新增的最终保护。

```sql
ALTER TABLE long_term_desire ADD COLUMN name_normalized TEXT NOT NULL DEFAULT '';
CREATE UNIQUE INDEX idx_long_term_desire_name_normalized
ON long_term_desire(name_normalized);
```

`desire_generation_attempt` 保存已经完成 LLM 调用和 JSON 解析、但正式欲望事务
尚未提交的结果。它属于欲望系统的本地恢复数据，不表示跨模块事件已完成。

```sql
CREATE TABLE desire_generation_attempt (
    id TEXT PRIMARY KEY,
    type TEXT NOT NULL,
    created_at REAL NOT NULL,
    peak_value REAL NOT NULL,
    seed TEXT,
    output_content TEXT NOT NULL
);
```

### 表达交互 attempt

`expression_interaction_attempt` 是表达系统的 durable 领域状态，不替代
`event_delivery`。它记录一次提问/主动搭话是否已被用户回答或已超时结算；状态转换必须用
条件更新。attempt 与对应的 `ASK` 或 `INITIATE_CHAT` 事件在同一本地事务中提交，
事务提交后才调用 `announce_committed`。

```sql
CREATE TABLE expression_interaction_attempt (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    source_id TEXT NOT NULL,
    correlation_id TEXT NOT NULL,
    text TEXT NOT NULL,
    created_at REAL NOT NULL,
    expires_at REAL NOT NULL,
    status TEXT NOT NULL,
    answered_at REAL,
    answer_event_id TEXT,
    failure_reason TEXT
);
```

索引为 `(status, expires_at)`、`(status, created_at)` 和 `(correlation_id)`。
它与总线 delivery 的恢复职责不同：delivery 负责消费者投递，attempt 负责表达领域内的
claim/answer/expire 幂等。

### `event_log` 展开状态

事件行需要能被 delivery expander 幂等扫描。可选择：

- 在 `event_log` 增加 `deliveries_expanded_at REAL`；
- 或通过查询 `event_delivery` 缺失记录推导；
- 或增加独立 outbox 表。

实现必须保证：进程在事件提交后、delivery 创建前崩溃，重启后能补齐 delivery。

## API 端点

本 spec 不新增用户可见 REST 端点。

现有写入口语义会变化：

- `POST /api/chat`：只有 durable admission 成功才返回 `{event_id}`；失败返回 503/429。
- `POST /api/observe`：同上。
- SSE 仍广播事件本身，不代表所有消费者完成。

可选后续调试端点需另写 spec，不在本轮默认新增。

## 测试要点

- [ ] DB 迁移：新库包含 `event_delivery`，必要时包含 `event_effect`；索引存在；迁移幂等。
- [ ] durable publish：publish 后即使不启动 worker，`event_log` 和 delivery 已落库；DB 失败时 publish 抛错且无半截记录。
- [ ] route expand：每个非空 `RouteSpec` 都创建对应 delivery；空路由事件只落 `event_log` 和 SSE，不创建消费者 delivery。
- [ ] handler 成功：delivery 从 `pending` 到 `processing` 到 `succeeded`，`completed_at` 写入。
- [ ] handler 失败：进入 `retry_wait`，attempts +1，`last_error` 写入，到期后恢复 pending。
- [ ] dead-letter：达到 `_DELIVERY_MAX_ATTEMPTS` 后进入 `dead_letter`，不再忙等重试。
- [ ] 失败隔离：`ACTIVITY_END` 的一个 consumer 失败不会让其它 consumer 重跑。
- [ ] FIFO：同一 consumer 按事件 timestamp/id 顺序执行；不同 consumer 可并行。
- [ ] lease 恢复：过期 `processing` 启动后恢复 pending，未过期不抢占。
- [ ] delivery 补齐：已有 event_log 但缺 delivery 时，启动扫描补齐且重复扫描不重复插入。
- [ ] 幂等：同一 `(event_id, consumer_id)` 重放不会重复应用模块状态变化。
- [ ] wake queue 满：已持久化事件不丢；dispatcher 扫描仍处理 pending。
- [ ] 数据库熔断：连续 admission 失败后新 publish 快速失败，冷却探测成功后恢复。
- [ ] drain 成功：停止新输入后，已受理事件完成，worker 停止，DB close 被调用。
- [ ] drain 超时：未完成 delivery 保留为可恢复状态，关闭流程不做全局 rollback。
- [ ] 路由单一来源：`ROUTING`、`TICK_ROUTING`、订阅 handler 和 delivery consumer 集合全部从 `RouteSpec` 派生并一致。
- [ ] 导入环回归：`api.routes`、`app_context`、`subscriptions`、`main` 不再形成循环导入。
- [ ] 文档同步：`docs/test-inventory.md` 更新为当前测试快照。

## 完成定义

- [ ] 本文件统一定义 DB 表、EventBus 投递、路由、组合根、REST/SSE、恢复重试和关停语义。
- [ ] `docs/tech-reference.md` 已同步实现文件、表数量、API 语义、路由数量和包结构。
- [ ] `docs/facts/module-bus-system-facts.md` 已同步当前事实。
- [ ] `ruff check` 零报错。
- [ ] `pyright` 零报错。
- [ ] `pytest` 全绿或用户确认的范围测试全绿。
- [ ] `docs/test-inventory.md` 已同步测试清单。
