# 模块与事件总线事实表

> 本文件是给“无关代码但会碰到模块通信、事件总线、组合根、DB 生命周期”的快速摘要。底层模块总线系统的唯一完整契约是 `docs/specs/05-module-bus-system.md`；本文件不复制完整 spec。修改 `nyx/events/`、`nyx/subscriptions.py`、`nyx/runtime.py`、`nyx/app_context.py`、`nyx/main.py`、事件相关 DB 表或跨模块副作用前，必须先读完整 spec，并同步更新本摘要。

## 当前实现事实

- 当前系统不是纯“模块只通过总线通信”：EventBus 负责事件受理、`event_log` 持久化、`event_delivery` 投递、SSE 广播和 handler 通知；Facade 之间仍存在直接查询/编排调用。
- `EventBus.publish(event)` 是 durable admission：根事件必须先持久化 `event_log` 和已注册 consumer 的初始 delivery，commit 成功后才返回；数据库不可用或总线关闭时抛受理错误。
- `event_log` 表示事件已发生；`event_delivery` 表示某个 `consumer_id` 对该事件的消费状态。不要用 `event_log` 推断消费者已完成。
- `EventBus.has_effect(event_id, consumer_id)` 可在外部调用前读取已提交 effect；最终幂等仍由事务内 `try_mark_effect_in_transaction` 保证。
- 投递语义是 at-least-once：消费者可能被重放，因此跨模块副作用必须按 `(event_id, consumer_id)` 幂等。
- 失败重放只针对失败消费者；已成功消费者不重复执行。
- 每个稳定 `consumer_id` 对应独立 FIFO worker；同一消费者内按事件时间/id 顺序执行，不同消费者不互相阻塞。
- handler 状态流转：`pending -> processing -> succeeded`；失败进入 `retry_wait`，超过上限进入 `dead_letter`。
- `processing` 带 `lease_until`；进程崩溃后租约过期的投递会恢复为 `pending`。
- `EventBus.recover_deliveries()` 会恢复过期 `processing`、到期 `retry_wait`，并为已有 `event_log` 补齐当前已注册 consumer 的缺失 delivery。
- 内存队列只做唤醒优化，不做事实来源；队列满不能丢失已经持久化的事件。
- SSE 每连接有界，满时丢最旧保最新；SSE 广播事件事实，不表示消费者完成。
- `RouteSpec` / `ROUTE_SPECS` 是路由运行时来源；`ROUTING` / `TICK_ROUTING` 是由它派生的兼容视图；`subscriptions.py` 从同一份 route spec 注册 handler。
- 当前已迁移的事务/幂等链：`ActivityLifecycle.start/complete/interrupt` 的活动状态、欲望状态和活动事件同事务提交；`DESIRE_EVAL -> DESIRE_GENERATED` 同事务提交；`desire.observation_state`、`desire.activity_end`、`inner_life.observation_state`、`inner_life.desire_satisfied`、`inner_life.activity_end`、`memory.activity_end`、`inner_life.reflection` 使用 `(event_id, consumer_id)` effect marker 防重放；`MemoryFacade.record_recall` 的升级和 `MEMORY_PROMOTED` 事件同事务提交。`inner_life.reflection` 的 LLM/解析在事务外完成，成功后的慢变量、effect marker 和 `REFLECTION_DONE` 在同一事务内提交，提交后才唤醒投递。
- 当前未完全迁移的链仍需谨慎：包含 LLM/文件等不可回滚副作用的路径还不能宣称完整 at-least-once 幂等；`memory.activity_end` 已有本地事务和 effect marker，但其内部 LLM 关系/矛盾判断仍属 best-effort 副作用。`USER_MESSAGE` 重放时若已存在同 correlation 的终局 `SPEAK/ASK` 事件会短路，但中途无终局事件的失败仍会重试。
- 数据库基础设施已有 `Database.close()`、`Database.transaction()`、锁/SQL 操作超时常量和基础熔断状态；不要新增绕过这些入口的长期连接管理。
- 关停采用有界 drain：先停止新输入，再等待已受理事件和 delivery 完成；超时保留未完成投递，下次启动恢复，不做伪全局回滚。
- 当前 `_App` 是组合根内部 dataclass，也承担运行期状态容器；不要把 `_App` 传入 Facade。

## 模块边界

- 允许 Facade 之间做只读查询：当前状态、待处理欲望、记忆、当前活动、运行期观察快照。
- 跨模块写副作用必须通过事件：例如 `ACTIVITY_END` 导致 desire、inner_life、memory 三方更新。
- 事件 payload 必须包含消费者完成工作所需的事实，不能依赖另一个消费者“刚好先跑完”。
- 反思检查 tick 只负责门槛判断并 durable publish `REFLECTION`，不直接调用反思逻辑；慢变量更新统一由 `inner_life.reflection` consumer 完成。
- `inner_life.reflection` 的解析失败会抛异常，delivery 进入 `retry_wait`；事务写入失败也会回滚并重试，不能把失败反思标记为成功。
- 如果两个消费者之间确实有先后依赖，应显式发布后续事件或合并到同一个消费者 FIFO，不依赖订阅顺序。

## 常用判断

- 看到“事件已落库，所以模块一定处理了”是错误判断；必须查 delivery。
- 看到“handler 失败被日志记录，所以可以忽略”是错误判断；目标架构必须 retry 或 dead-letter。
- 看到“数据库卡住但 API 继续返回 event_id”是错误行为；目标架构必须快速失败或明确未受理。
- 看到“为解决队列满直接丢业务事件”是错误行为；只有 `CLOCK_TICK` 等契约明确可合并事件才能合并/丢旧。
- 看到“新事件只改 `ROUTING` 或只改 `subscriptions.py`”是错误行为；必须改同一份路由来源并同步测试。
