# 模块与事件总线事实表

> 本文件是给“无关代码但会碰到模块通信、事件总线、组合根、DB 生命周期”的快速摘要。底层模块总线系统的唯一完整契约是 `docs/specs/04-module-bus-system.md`；本文件不复制完整 spec。修改 `nyx/events/`、`nyx/subscriptions.py`、`nyx/runtime.py`、`nyx/app_context.py`、`nyx/main.py`、事件相关 DB 表或跨模块副作用前，必须先读完整 spec，并同步更新本摘要。

## 当前实现事实

- 当前系统不是纯“模块只通过总线通信”：EventBus 负责事件受理、`event_log` 持久化、`event_delivery` 投递、SSE 广播和 handler 通知；Facade 之间仍存在直接查询/编排调用。
- 当前 `EventType` 有 25 个成员；阅读进度同步使用 `READING_PROGRESS`，不是新的消费者路由。
- `EventBus.publish(event)` 是 durable admission：根事件必须先持久化 `event_log` 和已注册 consumer 的初始 delivery，commit 成功后才返回；数据库不可用或总线关闭时抛受理错误。
- `EventBus.publish_many(events)` 将同一业务承诺的多条事件原子受理；任一冲突或失败整批回滚，
  已回滚行不计入 `persisted_count`，commit 后才广播/唤醒。快慢回复的全部正常文本只在终局
  一次提交；慢通道再与 `SPEAK/ASK`、`scene_memory_requested` 使用同一边界。
- 外部领域事务调用 `append_in_transaction()` 后失败时，`persisted_count` 按本事务实际新增数
  补偿，不恢复绝对快照，避免覆盖事务释放锁后其它发布增加的计数。
- `event_log` 表示事件已发生；`event_delivery` 表示某个 `consumer_id` 对该事件的消费状态。不要用 `event_log` 推断消费者已完成。
- `EventBus.has_effect(event_id, consumer_id)` 可在外部调用前读取已提交 effect；最终幂等仍由事务内 `try_mark_effect_in_transaction` 保证。
- 投递语义是 at-least-once：消费者可能被重放，因此跨模块副作用必须按 `(event_id, consumer_id)` 幂等。
- 失败重放只针对失败消费者；已成功消费者不重复执行。
- 每个稳定 `consumer_id` 对应独立 FIFO worker；同一消费者内按事件时间/id 顺序执行，不同消费者不互相阻塞。
- handler 状态流转：`pending -> processing -> succeeded`；失败进入 `retry_wait`，超过上限进入 `dead_letter`。
- handler 完成后的成功或失败状态 finalize 提交失败时，worker 原地重试 finalize，不重复调用 handler；关停后仍未完成的 processing 依赖下次启动 lease 恢复。
- `processing` 带 `lease_until`；进程崩溃后租约过期的投递会恢复为 `pending`。
- `EventBus.recover_deliveries()` 会恢复过期 `processing`、到期 `retry_wait`，并为已有 `event_log` 补齐当前已注册 consumer 的缺失 delivery。
- 内存队列只做唤醒优化，不做事实来源；队列满不能丢失已经持久化的事件。
- SSE 每连接有界，满时丢最旧保最新；SSE 广播事件事实，不表示消费者完成。
- `RouteSpec` / `ROUTE_SPECS` 是路由运行时来源；`ROUTING` / `TICK_ROUTING` 是由它派生的兼容视图；`subscriptions.py` 从同一份 route spec 注册 handler。
- 当前已迁移的事务/幂等链：Activity 来源领取、`RUNNING` 行与 `ACTIVITY_START` 同事务；完成、失败、中断和恢复把 Activity、关联欲望、关联任务与适用的活动事件同事务提交，`IDLE_REFLECTION` 完成时 `ACTIVITY_END` 与普通 `REFLECTION` 也共享该事务；`DESIRE_EVAL` 的周期压力由 `desire_eval_applied` 防重，生成与 `DESIRE_GENERATED` 同事务提交；`desire.observation_state`、`desire.activity_end`、`inner_life.observation_state`、`inner_life.desire_satisfied`、`inner_life.activity_end`、`memory.activity_end`、`memory.scene_reply`、`inner_life.reflection` 使用 `(event_id, consumer_id)` effect marker 防重放；普通新记忆与 `MEMORY_CREATED` 同事务；真实 recall 以 `(user_event_id, memory_id)` marker 防重，并与计数、升级和 `MEMORY_PROMOTED` 同事务。`inner_life.reflection` 与场景记忆的 LLM/解析在事务外完成，核心状态、effect marker 和终局事件同事务提交，提交后才唤醒投递。
- 委派任务与 Activity 的来源领取、终态和恢复均共享本地事务；完成、失败或中断事务回滚后，Facade 立即调和失去 runner 的 `RUNNING`，调和失败由下一次准入重试。取消 runner 最多等待 5 秒，超时保持原状态且不重排。
- 任务状态提交后再发布无 durable consumer 的 `TASK_UPDATED` 广播事件。广播、立即调度或调度后回读失败只记日志，不回滚已提交状态，也不把创建请求改成失败；前端还可用 REST 快照恢复。
- 当前未完全迁移的链仍需谨慎：包含 LLM/文件等不可回滚副作用的路径还不能宣称完整 at-least-once 幂等；`memory.activity_end` 已有本地事务和 effect marker，但其内部 LLM 关系/矛盾判断仍属 best-effort 副作用。`USER_MESSAGE` 重放时若已存在同 correlation 的终局 `SPEAK/ASK` 事件会短路，但中途无终局事件的失败仍会重试。
- 数据库基础设施已有 `Database.close()`、`Database.transaction()`、锁/SQL 操作超时常量和基础熔断状态；`connect()` 无显式参数或 `NYX_DB` 时优先复用已存在的旧默认 `nyx.db`，否则使用 `data/nyx.db`；非内存路径会先创建父目录；不要新增绕过这些入口的长期连接管理。
- 欲望系统额外使用 `desire_generation_attempt` 保存 LLM 已解析但尚未正式提交的结果及父长期欲望；`short_term_desire.parent_long_term_id` 与 attempt 的对应列都是可空外键，父记录删除时置空；`long_term_desire.name_normalized` 有唯一索引。数据库迁移和语义见 `04-module-bus-system.md` 与 `07-desire.md`。
- 组合根延迟依赖使用显式可空回调和带上下文的 `RuntimeError` 检查，不再使用可变列表下标占位；表达门面先构造、阅读门面后构造时，通过同样的窄回调延迟绑定
  `ReadingFacade.build_reply_context()`，不让两个 Facade 直接循环依赖。总线 supervisor 在
  `run()` 正常返回时结束。
- 表达系统额外使用 `expression_interaction_attempt` 持久化等待用户回应和主动行为承诺；attempt 与 `ASK`/`INITIATE_CHAT` 事件通过 `Database.transaction()` + `EventBus.append_in_transaction()` 同事务提交。用户领取时 `answer_event_id` 作为 claim owner，同 owner 可恢复；ANSWERED 与回复终局同事务，提交后再 announce 唤醒消费者。
- 表达系统的 fallback `SPEAK` 是正常终局事件：解析/评估失败不会吞掉为成功，fallback 发布失败会让用户消息 consumer 进入总线重试；同 correlation 已有终局 SPEAK/ASK 时重放应短路。
- 关停采用有界 drain：先停止新输入，再等待已受理事件和 delivery 完成；超时保留未完成投递，下次启动恢复，不做伪全局回滚。
- 当前 `_App` 是组合根内部 dataclass，也承担运行期状态容器；不要把 `_App` 传入 Facade。
- `/api/observe` 要求严格有限非负的 idle_seconds/sampled_at、一致的 presence 和最多 512 字符标题；采样不能晚于接收时间。presence_lock 串行化观察与消息，采样水位拒绝旧观察（409，不投递）和旧消息；durable publish 后才更新内存，未提交失败保留旧状态。publish 返回前取消时 is_durable 核实已提交观察并完成快照，再传播取消。锁内系统时钟回拨重建基线，不以旧事件创建时间推断回拨。
- 归来 pending/claimed 是进程内一次性事实，只含 returned_at/away_duration_seconds，通过同步回调注入 ExpressionFacade；领取在首次 await 前，finish/release 比对同一对象。正常终局提交立即消费，后续失败不能恢复；重新 away 清空旧 pending/claimed。重启初次观察不补造归来。
- SSE 公共头固定包含 `event_id`、`correlation_id`、后端 `Event.timestamp`，并在帧中发送标准 `id:` 游标；公共头覆盖同名 content 键。前端拒绝非法 timestamp，实时与历史均保留后端时间，不使用浏览器接收时间。
- `GET /api/events` 先注册实时 sink，再按 `Last-Event-ID` 回放最多 1000 条：已知游标只返回 `(timestamp, id)` 严格更晚的事件；未知或过期游标返回最早的一页有界事件。历史与实时之间允许重叠，前端按 `event_id` 去重。
- `GET /api/events/log` 的 `after` 使用同一游标和并列时间戳排序规则；省略 `after` 时仍使用管理查询的倒序。
- eval 的完整应用层 prompt 由 `eval_prompt` 按 `call_id` 永久明文保存；think/speak 两条 `eval_log` 共用一份。recent 列表不返回 prompt，详情通过精确 record id 懒加载，语义以 `10-eval.md` 为准。

## 模块边界

- 允许 Facade 之间做只读查询：当前状态、待处理欲望、记忆、当前活动、运行期观察快照。
- 跨模块写副作用必须通过事件：例如 `ACTIVITY_END` 导致 desire、inner_life、memory 三方更新。
- 事件 payload 必须包含消费者完成工作所需的事实，不能依赖另一个消费者“刚好先跑完”。
- “行为承诺后再做派生副作用”同样适用于主动搭话：欲望先原子 claim，attempt + `INITIATE_CHAT` 提交后才允许打断活动；打断失败不能撤销已提交搭话。
- 反思检查 tick 只负责门槛判断并 durable publish `REFLECTION`，不直接调用反思逻辑；慢变量更新统一由 `inner_life.reflection` consumer 完成。
- 四个正式 producer 使用同一 `REFLECTION`：周期 `periodic`、记忆矛盾 `memory_contradiction`、阅读重访 `reading_revisit`、闲置活动 `idle_activity`。payload 持久化触发证据；消费时复核事件时效，周期请求还复核叙事 revision、冷却和记忆门槛。已过期请求只写 effect，不产生情感或完成事件。
- `inner_life.reflection` 的解析失败会抛异常，delivery 进入 `retry_wait`；叙事/长期欲望快照冲突或事务写入失败也会回滚并重试，不能把失败反思标记为成功。
- 如果两个消费者之间确实有先后依赖，应显式发布后续事件或合并到同一个消费者 FIFO，不依赖订阅顺序。

## 常用判断

- 看到“事件已落库，所以模块一定处理了”是错误判断；必须查 delivery。
- 看到“handler 失败被日志记录，所以可以忽略”是错误判断；目标架构必须 retry 或 dead-letter。
- 看到“数据库卡住但 API 继续返回 event_id”是错误行为；目标架构必须快速失败或明确未受理。
- 看到“为解决队列满直接丢业务事件”是错误行为；只有 `CLOCK_TICK` 等契约明确可合并事件才能合并/丢旧。
- 看到“新事件只改 `ROUTING` 或只改 `subscriptions.py`”是错误行为；必须改同一份路由来源并同步测试。
