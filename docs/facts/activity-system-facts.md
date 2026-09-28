# 活动系统事实表

> 本文件是给“无关代码但会碰到活动事实”的快速摘要，例如表达打断活动、欲望满足回写、内在生命读取当前活动、前端活动时间线、读书/探索落记忆。完整活动契约统一在 `docs/specs/09-activity.md`。修改 `nyx/activity/` 或活动契约时必须先读完整 spec，并同步更新本摘要。

## 模块边界

- `ActivityFacade` 是活动系统唯一门面：除活动生命周期、查询与材料登记外，还提供 `list_tasks`、`assign_web_task`、`assign_book_task` 三个委派任务入口。
- `ActivityStarter` 负责“空闲时启动什么”：单 task 守卫、查 RUNNING、同日程块 PAUSED 恢复、委派任务领取、欲望排序、跨书库选材和默认活动。
- `ActivityLifecycle` 负责状态转换和副作用：`start`、`complete`、`fail`、`interrupt`，并发布 `activity_start`、`activity_end`、`activity_interrupted`。
- `ActivityStore` 管理 `activity` 与 `assigned_task` 表，并用 `list_unfinished()` 提供启动恢复候选；`MaterialStore` 只管 `material` 表。Facade 不直接写 SQL。
- `ReadingActivityRunner` 执行活动系统自己的分块读书；`Exploration` 执行自由探索；`creation.py`、`observe.py`、`screen.py` 提供对应活动的纯函数或旁路能力。
- `reading/` 是陪读系统，使用 `books` / `paragraphs` / `reading_progress`；`activity/material` 是 Nyx 自己读的分块文本书库。两套书库继续并行持有数据，但探索欲选材会统一枚举二者。

## 触发与调度

- `runtime.tick_loop` 发布 `CLOCK_TICK`；`subscriptions.py` 按 `RouteSpec.tick_type` 把 `SCHEDULE_BLOCK_START` 路由到 `runtime.on_schedule_block_start()`，再调用 `activity.on_tick`。
- `DESIRE_GENERATED` 事件订阅到 `activity.on_desire_generated`，新欲望出现时也会尝试启动活动。
- `_maybe_start_activity` 不 await 完整活动，只创建后台 task，避免 EventBus 被 LLM、文件读取或探索链阻塞。
- 同一进程内唯一活动靠两层守卫：`ActivityStarter._lock` 串行化启动决策，`ActivityFacade._task` 在锁内赋值并用于关闭 PENDING 到 RUNNING 之间的窗口。
- 空闲选择顺序是：当前后台 task / RUNNING、同块 PAUSED、低精力 REST、最早 PENDING 委派任务、短期欲望、默认活动。领取任务与插入活动在同一 SQLite 事务内完成。
- `ActivityStore.get_current()` 只返回 status 为 `running` 的最新活动；PENDING 不算 current。
- `get_schedule()` 返回今天已产生的活动记录，按 `started_at ASC`；今天的起点由 `_day_start(time.time())` 按运行电脑的系统本地时区计算自然日 `00:00`。
- `schedule_block_id(now, grid_minutes)` 先按 grid 分桶，再格式化成 `HH:MM`。不要用格内原始分钟拼标签。

## 欲望到活动

- `rank_desires` 按类型级 `expression_weight` 降序、同权按 `created_at` 升序。
- `desire_to_activity` 映射：`EXPLORATION -> READING`、`CREATION -> CREATION`、`REST -> REST`、`INTERACTION -> None`。
- `build_schedule` 在精力低于 `ENERGY_REST_THRESHOLD` 时先插入 `REST`，再安排欲望活动；它只输出活动类型序列，不写库。
- `select_activity` 返回 `Activity | None`：无欲望或全互动欲返回 None；精力不足时可能先返回无 desire 关联的 REST。
- `progress` 里保存欲望关联：`desire_id`、`goal`、`correlation_id`、`description`。当前实现把 `correlation_id` 设为欲望 id。
- `READING` 活动由探索欲或委派任务产生。探索欲有非空 `goal.topic` 时，对 EPUB 书名/文件名与 material 文件名使用同一归一化模糊评分；没有匹配且过自由探索限速时才升级 `FREE_EXPLORATION`，不再回退到最近上传材料。没有 topic 时走默认活动。

## 生命周期

- 新活动先以 `PENDING` 插入 activity 表，后台 `_execute` 开始后由 `ActivityLifecycle.start` 改为 `RUNNING` 并发布 `activity_start`。
- app context 启动后会调用 `ActivityFacade.recover_stale_running()`：DB 遗留 PENDING 一律转 `ABANDONED` 并释放关联 ACTIVE desire；遗留 RUNNING 中，`READING` / `CREATION` / `FREE_EXPLORATION` 转 `PAUSED`，瞬时活动转 `ABANDONED`，关联 desire 标 `SUPPRESSED`，不发布新的打断事件。
- 启动恢复在活动恢复后把没有执行者承接的 RUNNING 委派任务重置为 PENDING；委派活动被打断时任务也回到 PENDING，同块恢复时重新标 RUNNING。
- `start` 会把关联的 PENDING desire 标为 ACTIVE；无 `desire_id` 的默认活动不碰 desire。
- 正常结束由 `complete` 改为 `COMPLETED`，写 `ended_at`，发布 `activity_end`。
- 异常结束由 `fail` 改为 `INCOMPLETE`，写 `ended_at`，并把 ACTIVE desire 标为 SUPPRESSED；异常继续上抛，由 task done callback 收割。
- 打断由 `interrupt(activity_id, by_event)` 执行：确认目标存在且 RUNNING，cancel 当前 task，await 结束后重读；若仍 RUNNING，可续类型转 PAUSED，其他类型转 ABANDONED。
- 当前可续类型是 `READING`、`CREATION`、`FREE_EXPLORATION`。
- 打断会把 ACTIVE desire 标为 SUPPRESSED，并发布 `activity_interrupted`。
- `activity_start` / `activity_interrupted` / `activity_end` 都优先使用活动 progress 中的 `correlation_id`，缺失时回退 activity id。

## 完成与满足

- `activity_end` content 形状为 `{activity_id, type, desire_id, goal_met, energy_delta, result}`。
- `goal_met(goal, result)` 当前代码语义：`goal is None -> True`。
- `ActivityLifecycle.complete` 先读 `activity.progress["goal_signal"]`；值为 `bool` 或 `None` 时直接作为 `activity_end.goal_met`，否则调用 `goal_met(goal, result)`。
- `read` 目标只有 `result.completed` 为真才算满足；material 与探索欲 EPUB 自动分块读完
  一块但未读完整本时都会设置 `goal_signal=None` 并发布 `goal_met=None`，表示有进展但不
  结算欲望、不增加 retry。
- `write` 目标要求 result 同时有 `title` 和 `content`。
- `observe` 目标要求 result 有 `presence`。
- `free_exploration` result 若 `outcome == "won"`，即使 goal action 是 read，也会被视为满足。
- `DesireLifecycle.satisfy_from_activity_end` 只在 `desire_id` 是 str 且 `goal_met` 是 bool 时调用 `satisfy`；`goal_met=None` 或无 desire_id 会被跳过。
- `satisfy(goal_met=True, goal 非 None)` 会累计 `goal_progress+1`，达到 `goal.count` 后才 SATISFIED。
- `satisfy(goal_met=False)` 会 `retry_count+1`；超过 retry limit 后 EXPIRED，否则保持 PENDING。
- `activity_end.type` 为 `reading` 或 `free_exploration` 时，满足逻辑之外还会给创造欲加压。

## 活动执行

- `READING` 必须有真实 `source`；缺 source 会 `raise ValueError`，防止 LLM 凭空编造读书内容。
- 明确网页任务只调用 `web_fetch`，正文按 6000 字符块沉淀 knowledge 和滚动 profile；checkpoint 以 pending 两阶段提交，失败或空正文把任务置为 FAILED。
- EPUB 委派任务与探索欲命中的 EPUB 都调用 `ReadingFacade.read_for_activity()`：复用原文沉淀、只单调推进 Nyx 进度，不修改用户进度，也不触发陪读冲动、提问或联想。
- `ReadingActivityRunner` 每次读取 `source` 的 `[read_chars, read_chars+6000)` 文本块，调用
  LLM 生成 `{book, note}`，并在追加 fragment、推进 `material.read_chars` 之前沉淀本块原文。
  `material.memory_state` 保存 `processed_to`、滚动 profile 和 pending；恢复时先复用 pending，
  不重复调用原文提取 LLM，也不把未沉淀的字符标成已读。
- 每个材料块生成最多 5 条带 `material:` 来源 topic 的 knowledge；先持久化 pending，再写
  knowledge，最后推进 `processed_to`。知识写入后崩溃可重放，统一来源内去重吸收重复。
- 读到末尾或 chunk 为空时，runner 只聚合已有片段并写入
  `workspace/notes/<filename>-<path-hash>.md`；知识已经逐块沉淀，不再重扫全文。
- `CREATION` 按 goal topic/描述融合召回最多 3 条相关 knowledge/历史创作，连同当前观察、情绪、精力、审美和 canon 调用 LLM；`title/content` 必须是非空字符串。文件经 `ToolRegistry` 写入 `workspace/creations/<safe-title>-<activity-hash>.md`，标题 stem 最长 96 字符，同名活动不覆盖；同路径同内容写入幂等。进度写在 `activity.progress["creation"]`，恢复时复用 style / LLM 结果 / 文件路径。
- `FREE_EXPLORATION` 由 `Exploration.run(activity)` 执行显式 checkpoint 状态机：`searching -> reading_results -> summarizing -> sinking -> completed`。进度写在 `activity.progress["exploration"]`，恢复时从 cursor 继续抓取结果；`sink_done=true` 时不重复新增长期欲望或 knowledge 记忆。
- 探索的 web 结果先用 `web_fetch` 读取正文，本地结果先用 `file_io(read)` 读取文件，失败才
  回退 snippet；每条正文最多取 6000 字符进入 finding 和原文沉淀。URL/路径生成稳定的
  `web:` / `local:` topic；`source_pending` 在写 knowledge 前落 activity checkpoint，写完
  才推进 cursor，因此恢复不会重复抓取或丢失来源知识。正文和 snippet 都为空白时跳过
  `digest_source_block` 与 finding/knowledge，但 cursor 继续推进。
- `OBSERVE_USER` 读取组合根维护的 `last_presence`、`last_window_title`、`last_screen_summary`，用纯函数拼 summary。
- `classify_presence(idle_seconds)` 的边界为 `<30` 秒 online、`30-300` 秒 busy、`>=300` 秒 away，窗口标题不参与三态判定。
- Windows Tauri command `sample_presence` 返回系统空闲毫秒和前台窗口标题；原生采样失败或非 Windows 拒绝命令，前端降级为 WebView 输入时间，标题为空，不使用 `document.title`。
- WebView 闲置使用单调时钟，sampled_at 使用系统 epoch；时刻回拨不能被 unchanged 去重吞掉。
- 前端每 30 秒采样，single-flight 保留最新待上报值，成功请求才推进 last-sent，失败下轮重试；WebView 降级只观察窗口内输入，不具有系统级输入感知。
- `_App` 首次观察只建立基线，away 起点从 sampled_at 回溯到最后输入时刻；away→online 产生一次性归来，较新的 durable USER_MESSAGE 在表达前也提供 online 证据。水位阻止旧采样/消息倒灌，重新 away 废弃旧归来；SSE 重连重采样，前端请求有超时与乱序保护。归来不强制发言。
- 昼夜是本地 22:00-06:00，只影响表达与前端视觉，不改变活动能耗或内在生命数值。
- `IDLE_REFLECTION` 通过组合根注入的 `reflect` 回调执行反思活动；阅读重读触发的反思不走直接调用，而是发布 durable `REFLECTION` 事件。
- `REST` 返回空 result。

## API 与前端

- `GET /api/activity` 返回 `{current, schedule}`，分别来自 `ActivityFacade.get_current()` 与 `get_schedule()`。
- `GET /api/activity/results` 返回已完成且带产出的活动，按 `ended_at DESC`；`limit=1..100`、`offset=0..100000` 在 SQL 层分页，可用 `activity_type=creation` 只取创作，越界参数返回 422。省略类型参数时保持返回 `reading`、`free_exploration`、`creation` 的兼容行为；`activity(status, type, ended_at DESC)` 复合索引支撑创作历史查询。
- `GET /api/tasks` 返回任务快照；`POST /api/tasks/web` 和 `/api/tasks/book` 创建网页/EPUB 任务。活动页提供网页/书籍分段表单、目标段前后预览及任务状态/失败原因。
- 前端活动页的产出区只列最近 12 条创作标题摘要；独立“创作”页按 12 条一批加载，正文默认折叠，展开后显示完整正文与文件路径。
- `activityStore.refresh()` 会推进创作结果请求世代；更早发出的“加载更多”响应或错误不得改写刷新后的首页、错误或 loading 状态。
- `POST /api/upload` 读取文本上传，写入 `workspace/uploads/<filename>`，再调用 `activity.register_material(path, filename, len(text))`；它只注册书库，不立即启动读书。
- `GET /api/materials` 返回 `{materials}`，供资料面板展示 activity/material 书库进度。
- `activity_start`、`activity_end`、`activity_interrupted` 都经 EventBus 持久化并广播到 SSE；任务状态变化另发 `task_updated`，前端据此重拉活动与任务快照。事件 payload 由 `event.content` 展开并附加 `event_id`、`correlation_id`、后端 `timestamp`。

## 重构后状态机事实

- 分块读书的“读了一块但没读完整本”是 `goal_met=None`，不再按失败重试。
- 进程重启后的遗留 RUNNING 已由 app context 启动清理，不会永久阻塞新活动。
- `goal None -> True` 是当前代码语义；可续 runner 可用 `progress["goal_signal"]` 覆盖本次 `activity_end.goal_met`。
- 可续活动 checkpoint / resume / finalize 契约由各 runner 负责，打断只负责状态切换。
