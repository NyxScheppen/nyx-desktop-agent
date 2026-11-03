# 模块与事件总线事实表

> 本文件是给“无关代码但会碰到模块通信、事件总线、组合根、DB 生命周期”的快速摘要。底层模块总线系统的唯一完整契约是 `docs/specs/04-module-bus-system.md`；本文件不复制完整 spec。修改 `nyx/events/`、`nyx/subscriptions.py`、`nyx/runtime.py`、`nyx/app_context.py`、`nyx/main.py`、事件相关 DB 表或跨模块副作用前，必须先读完整 spec，并同步更新本摘要。

## 当前实现事实

- 阶段五将受声明限制的能力上下文、稳定拓扑、构建与启动校验移至独立 `packages/nyx-harness`；Nyx 业务回调、Facade 工厂、工具和17条路由仍在 `nyx/plugins.py`，总线与宿主行为不变，完整边界见24契约。

- OneBot正向WebSocket仅接指定账号私聊；稳定USER_MESSAGE根ID复用总线去重，不改桌面选择/presence。
  schema51增加transport_outbox/checkpoint两表（共45张业务/基础设施表，SQLite总48）；schema52为 event_log 增加显式 event_seq，替代运行时依赖 SQLite rowid。
  首次装配从日志末尾起步，按event_seq扫描THINK/SPEAK/ASK入队/echo确认分别与游标同事务。
  THINK发送时添加“💭 内心话”前缀，事件原文/游标不改，已扫描历史不补发。发送至少一次，断线续发。
  传输任务随角色监督和停止，网络错误重连，DB错误传播；writer未退出不关库。详见21契约。

- 聊天的 `discussion` 是严格 bool/null 三态：true 强制讨论、false 禁用讨论、null/省略自动；随 USER_MESSAGE 持久化并经 runtime 传给表达层，旧事件缺失按 null 重放。
- 组合根在 memory 构建后注册三个讨论能力工具，再由 `bootstrap.load_skills` 读取 JSON、校验名称/片段/已注册工具并注入表达门面；坏声明或缺失技能拒绝启动。P2 已增加 schema 43 的证据块 location/embedding 与事实 source_chunk_id。P3 `deep_research` 注入原始 search/fetch、共享 llm、memory 摄入与已有 Evaluator；研究输出解析前进入既有 token/调用历史/prompt 记录，失败隔离且等待纳入研究时限。关闭联网用空 handler 显式降级，工具仍注册，无新增事件类型、API 或持久任务。
- memory 先于工具装配：注册的 `web_fetch` 注入摄入回调，checkpoint 阅读器仅在 web_enabled 时注入 raw fetch，避免重复摘要及恢复时绕过联网开关。`knowledge_search` 统一调用 MemoryFacade 双层检索。
- `POST /api/knowledge/sources` 复用角色写请求准入和 selection revision，不新增持久事件种类；各块先提交证据，再生成知识。阅读/探索通过原文及提取 pending 恢复，最终 cursor/profile/清 pending 原子提交；不再要求证据随最终 cursor 回滚。
- schema 45 在同一迁移事务中将旧 WEB checkpoint 顶层字符串 source_text 搬到 `assigned_task_source`，移除原字段并推进版本；失败回滚。网页正文按任务 ID 首次提交一次，任务删除时 CASCADE，后续进度只写 cursor/profile/当前块 pending。抓取仍在事务外，不新增 API 或事件路由。

- 当前系统不是纯“模块只通过总线通信”：EventBus 负责事件受理、`event_log` 持久化、`event_delivery` 投递、SSE 广播和 handler 通知；Facade 之间仍存在直接查询/编排调用。
- 当前 `EventType` 有 26 个成员；阅读进度同步使用 `READING_PROGRESS`，不是新的消费者路由。
- schema47/48 增加 `tool_call_log`（三索引）和 `lesson_signal`，schema49 增加工具 checkpoint 两表，schema50 增加临时 tool_execution 回执，schema51增加传输两表，schema52增加 `event_log.event_seq`，业务及基础设施表合计45张（SQLite表总数48）。PostgreSQL 角色库由官方 `AsyncPostgresSaver` 创建其 `checkpoint_migrations`、`checkpoints`、`checkpoint_blobs`、`checkpoint_writes` 表，不复用 SQLite 的 `writes` 结构。
  ToolRegistry 的观测独立提交、失败不改变工具结果；Evaluator 的 OOC 回调经组合根接到记忆经验入口，
  不增加事件路由，技术失败不触发内心反思。
- 表达子图围绕工具调用可靠领取并完成 tool_execution 回执；完整临时结果用于恢复，回执读写错误传播，独立于 best-effort 观测。恢复复用成功结果，未确认副作用进入 uncertain 停批；终局 checkpoint 删除后清理回执，启动补清终局残留。
- P4 增加 `USER_MESSAGE / memory.annotation_recording` 和
  `ANNOTATION_FEEDBACK / memory.annotation_feedback` 两条独立 durable 路由。表达终局短路不跳过录入；
  recording 将 schema46 `annotation_feedback` 行、canonical 反馈事件和 effect 同事务提交。
  mediation 在事务外完成 LLM/eval，在事务内提交受限事实和 effect；提交后才发 best-effort 学习信号。
  同类三次纠错且经验沉淀成功后，REFLECTION/清除计数同事务；提交与信号间中断可丢学习，
  effect 重放不补发。append 回滚按实际增量补偿 `persisted_count`；提交后 announce 丢失可由扫描恢复。
  无批注旧消息 no-op；失败沿用最多5次投递与 dead_letter，不增加无限重试器。
- Chat API 的 `annotation_persistence=recorded|pending` 只确认反馈行；SSE 的 `annotation_feedback`
  也只表示已记录，不代表事实或反思完成。锚点经 `EventBus.get_event(event_id)` 精确读取当前角色库，
  不选择最新回复；已受理快照重放不重查锚点。
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
- handler 完成后的成功或失败状态 finalize 提交失败时，worker 原地重试 finalize，不重复调用 handler；崩溃遗留 processing 依赖 lease 恢复。证明旧 worker/runner 全部结束的干净停止会立即释放残留 processing，避免挂载后永久阻塞 FIFO。
- `processing` 带 `lease_until`；进程崩溃后租约过期的投递会恢复为 `pending`。
- `EventBus.recover_deliveries()` 会恢复过期 `processing`、到期 `retry_wait`，并为已有 `event_log` 补齐当前已注册 consumer 的缺失 delivery。
- 内存队列只做唤醒优化，不做事实来源；队列满不能丢失已经持久化的事件。
- durable consumer 的通知队列有界、队满丢唤醒提示但已提交日志不丢；每个 SSE reader 独立按 event_log.event_seq 提交序扫描、每页1000条，首次取最近一页，游标未知时自开头分页。读取队列只唤醒 reader，15秒补扫一次；帧游标仍为 character_id:event_id，timestamp 仍是事件原始时间。详见22契约。
- `RouteSpec` / `ROUTE_SPECS` 是路由运行时来源；`ROUTING` / `TICK_ROUTING` 是由它派生的兼容视图。
  `nyx/plugins.py` 集中声明七个域组件与 tools 的依赖、工具、路由和生命周期，完整契约见 spec 20。
  装配时校验插件路由与现有17条完整 RouteSpec 相等；`subscriptions.py` 从同一插件声明绑定 handler，
  按既有 `ROUTE_SPECS` 顺序注册，再调用 `bus.validate_routes()`。
- 当前已迁移的事务/幂等链：Activity 来源领取、`RUNNING` 行与 `ACTIVITY_START` 同事务；完成、失败、中断和恢复把 Activity、关联欲望、关联任务与适用的活动事件同事务提交，`IDLE_REFLECTION` 完成时 `ACTIVITY_END` 与普通 `REFLECTION` 也共享该事务；`DESIRE_EVAL` 的周期压力由 `desire_eval_applied` 防重，生成与 `DESIRE_GENERATED` 同事务提交；`desire.observation_state`、`desire.activity_end`、`inner_life.observation_state`、`inner_life.desire_satisfied`、`inner_life.activity_end`、`memory.activity_end`、`memory.scene_reply`、`inner_life.reflection` 使用 `(event_id, consumer_id)` effect marker 防重放；普通新记忆与 `MEMORY_CREATED` 同事务；真实 recall 以 `(user_event_id, memory_id)` marker 防重，并与计数、升级和 `MEMORY_PROMOTED` 同事务。`inner_life.reflection` 与场景记忆的 LLM/解析在事务外完成，核心状态、effect marker 和终局事件同事务提交，提交后才唤醒投递。
- 委派任务与 Activity 的来源领取、终态和恢复均共享本地事务；完成、失败或中断事务回滚后，Facade 立即调和失去 runner 的 `RUNNING`，调和失败由下一次准入重试。取消 runner 最多等待 5 秒，超时保持原状态且不重排。
- 任务状态提交后再发布无 durable consumer 的 `TASK_UPDATED` 广播事件。广播、立即调度或调度后回读失败只记日志，不回滚已提交状态，也不把创建请求改成失败；前端还可用 REST 快照恢复。
- 当前未完全迁移的链仍需谨慎：包含 LLM/文件等不可回滚副作用的路径还不能宣称完整 at-least-once 幂等；`memory.activity_end` 已有本地事务和 effect marker，但其内部 LLM 关系/矛盾判断仍属 best-effort 副作用。`USER_MESSAGE` 重放时若已存在同 correlation 的终局 `SPEAK/ASK` 事件会短路，但中途无终局事件的失败仍会重试。
- 数据库基础设施已有 `Database.close()`、`Database.transaction()`、锁/SQL 操作超时常量和基础熔断状态；`connect()` 无显式参数或 `NYX_DB` 时优先复用已存在的旧默认 `nyx.db`，否则使用 `data/nyx.db`；非内存路径会先创建父目录。迁移失败或取消时捕获 BaseException，关闭尚未交给宿主的连接后重抛；不要新增绕过这些入口的长期连接管理。
- 欲望系统额外使用 `desire_generation_attempt` 保存 LLM 已解析但尚未正式提交的结果及父长期欲望；`short_term_desire.parent_long_term_id` 与 attempt 的对应列都是可空外键，父记录删除时置空；`long_term_desire.name_normalized` 有唯一索引。数据库迁移和语义见 `04-module-bus-system.md` 与 `07-desire.md`。
- 组合根按插件强依赖稳定拓扑序构造组件，`PluginContext` 只读能力视图替代手工 late-bound 闭包。
  reading/state/eval 三处弱边及观察/归来窄回调全部绑定后，先统一校验必要弱依赖，再调用 start，
  恢复活动与表达历史；阅读上下文缺失仍降级为空串。Facade 不接收 `_App`，其字段与宿主生命周期不变。
  装配失败或取消仍关闭 Database。总线 supervisor 在 `run()` 正常返回时结束。
- 表达系统额外使用 `expression_interaction_attempt` 持久化等待用户回应和主动行为承诺；attempt 与 `ASK`/`INITIATE_CHAT` 事件通过 `Database.transaction()` + `EventBus.append_in_transaction()` 同事务提交。用户领取时 `answer_event_id` 作为 claim owner，同 owner 可恢复；ANSWERED 与回复终局同事务，提交后再 announce 唤醒消费者。
- 表达系统提供 `GET /api/questions/pending` 读取仍在等待的 attempt 和服务端当前时间，`POST /api/questions/{attempt_id}/pause` 与 `/resume` 通过条件更新暂停/恢复计时；这些辅助端点不发布 EventBus 事件。暂停项保留 `paused_at`，不参与超时领取；恢复时按暂停时剩余时长重算 `expires_at`。用户消息若带 `reply_to` 只绑定指定 attempt；未带时仅有一个等待项才自动绑定，多个等待项不猜测。
- 运行时在欲望搭话准入前读取 waiting attempts；存在待回答交互时跳过本轮搭话。组合根创建表达门面后从各类型 durable 表达事件恢复有界 history，再启动角色运行逻辑。
- 表达系统的 fallback `SPEAK` 是正常终局事件：解析/评估失败不会吞掉为成功，fallback 发布失败会让用户消息 consumer 进入总线重试；同 correlation 已有终局 SPEAK/ASK 时重放应短路。
- 普通卸载采用有界 drain，超时不停止活 worker，保留 STOPPING/DB/名额，由宿主继续收尾；宿主 close 则保持事件循环存活，反复收尾全部角色，连接全部关闭后才返回，避免 aiosqlite 线程阻止进程退出。既有桌面父进程退出期限仍可终止无法合作的后端。
- 当前 `_App` 是组合根内部 dataclass，也承担运行期状态容器；不要把 `_App` 传入 Facade。
- 每角色另外运行独立的记忆维护循环（60 秒检查），与现有 CLOCK_TICK 分开；卸载时先取消并等待该循环，再关闭数据库。schema 42 新增对话段召回标记和事实来源列，不新增 EventBus 路由。
- `/api/observe` 要求严格有限非负的 idle_seconds/sampled_at、一致的 presence 和最多 512 字符标题；采样不能晚于接收时间。presence_lock 串行化观察与消息，采样水位拒绝旧观察（409，不投递）和旧消息；durable publish 后才更新内存，未提交失败保留旧状态。publish 返回前取消时 is_durable 核实已提交观察并完成快照，再传播取消。锁内系统时钟回拨重建基线，不以旧事件创建时间推断回拨。
- 归来 pending/claimed 是进程内一次性事实，只含 returned_at/away_duration_seconds，通过同步回调注入 ExpressionFacade；领取在首次 await 前，finish/release 比对同一对象。正常终局提交立即消费，后续失败不能恢复；重新 away 清空旧 pending/claimed。重启初次观察不补造归来。
- SSE 公共头固定包含 `event_id`、`correlation_id`、后端 `Event.timestamp`，并在帧中发送标准 `id:` 游标；公共头覆盖同名 content 键。前端拒绝非法 timestamp，实时与历史均保留后端时间，不使用浏览器接收时间。
- `GET /api/events` 使用独立 SQLite reader 按提交序续传，reader 互不共享游标；逐行读日志后只推进本连接位置。前端只以完整SSE帧更新Last-Event-ID并按 event_id 去重。
- `GET /api/events/log` 的 `after` 使用同一游标和并列时间戳排序规则；省略 `after` 时仍使用管理查询的倒序。
- eval 的完整应用层 prompt 由 `eval_prompt` 按 `call_id` 永久明文保存；think/speak 两条 `eval_log` 共用一份。recent 列表不返回 prompt，详情通过精确 record id 懒加载，语义以 `10-eval.md` 为准。

## 模块边界

- 角色磁盘库在 `connect()` 内建立独立的 AsyncSqliteSaver 连接，受 shield 保护的打开过程完成后才能传播取消并清理；Database.close 同时关闭 saver 和业务连接。内存库使用 InMemorySaver，宿主库无 saver。工具子图按原 correlation 恢复；启动补清已 SPEAK/ASK 的 checkpoint，未完成项由既有 USER_MESSAGE delivery 恢复。

组合根在 ActivityFacade 构造后注册 `browser_survey` 表达工具；`POST /api/tasks/browser-survey` 与该工具共享活动任务入口。问卷运行中普通用户消息和主动搭话不会打断浏览器会话；关停或其他中断后任务失败，不重放已点击的选项。

- `_Host` 是注册、当前选择、共享模型/embedding/屏幕观察和 durable presence 的宿主；每个角色独立 `_App`/Database/EventBus/Facade/工作目录、熔断和恢复预算，切换不重建运行时。
- 宿主 `/api/observe` 要求稳定 sample_id，先保存事实和接收者，再按每角色 FIFO 独立补投；角色事件/顺序水位/转换快照同事务。重试复用原时钟判定；恢复基线不改历史事件身份。
- 角色业务路径为 `/api/characters/{id}/…`，写请求准入检查 selection revision 后捕获运行时。SSE 带 character_id，游标为 `id:event_id`（Nyx 兼容别名同样使用组合游标）。下文未带命名空间的路径是角色业务后缀。
- 可选 NYX_AUTH_TOKEN 在宿主和角色 guard 保护 REST、SSE 与角色图片；NYX_PUBLIC_ORIGIN 仅接受 HTTPS origin，且要求设置 token。配置公网 origin 时，/api Host 必须匹配其主机与端口；未配置时仅接受本机 localhost:8000。/api/ready 仅供本地 launcher，根 Caddyfile 不代理该路由。阶段三契约见22。
- `EventBus.run()` 监督意外退出的 consumer worker，恢复阶段也在 finally 清理范围内；只重启所属角色，不改变五次 delivery 和 finalize-only 语义。

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
