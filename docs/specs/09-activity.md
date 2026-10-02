# 活动系统：日程排期、委派任务与状态机

> 本文件是活动系统的唯一完整契约。它合并日程排期、活动 Facade、活动生命周期、委派任务、统一阅读选材、可续活动状态机、自由探索、观察与屏幕视觉的规格。
> `docs/facts/activity-system-facts.md` 只是供无关代码查阅的事实摘要，不替代本文件；摘要与本文件不一致时，以本文件为准，并同步修正摘要。
> spec 只定义契约（签名、语义与决策），不内联完整代码；实现以 `nyx/activity/` 源文件为准。

## 元信息

- **前置依赖**：01-types（`Activity` / `ActivityType` / `ActivityStatus` / `AssignedTask` / `AssignedTaskType` / `AssignedTaskStatus` / `DesireType` / `ShortTermDesire` / `DesireValue` / `CurrentState` / `Event` / `EventType`）、02-config（`ActivityConfig` / `ExplorationConfig` / `ActivityEnergyDelta`）、03-llm（`LlmClient.complete` / `VisionClient`）、04-module-bus-system（`activity` / `assigned_task` 表、`EventBus` / `internal_event` / tick 路由、组合根、REST、SSE）、05-tools（`ToolRegistry`）、06-memory-system（活动/知识记忆落库）、07-desire（待消费欲望、活动状态接线、满足回写、长期欲望入口）、08-inner-life（状态快照、反思、精力变化）、10-eval（`Evaluator`）、12-reading-system（EPUB 读取入口）
- **实现文件**：`nyx/activity/scheduler.py`、`nyx/activity/store.py`、`nyx/activity/starter.py`、`nyx/activity/lifecycle.py`、`nyx/activity/facade.py`、`nyx/activity/creation.py`、`nyx/activity/llm_result.py`、`nyx/activity/paths.py`、`nyx/activity/exploration.py`、`nyx/activity/observe.py`、`nyx/activity/screen.py`、`nyx/db.py`、`nyx/api/routes.py`、`nyx/app_context.py`、`frontend/src/api/client.ts`、`frontend/src/stores/activityStore.ts`、`frontend/src/components/panels/ActivityPanel.tsx`、`frontend/src/types/api.ts`

## 用户故事

> 作为 Nyx 系统的开发者，我想让活动系统把欲望从「生成」推进到「消费、执行、结算或恢复」，并把活动进度、打断原因和产出广播给前端，以便欲望闭环、活动时间线和可续工作都能稳定运行。

> 作为用户，我想把明确网页或书架中的 EPUB 阅读目标交给 Nyx，让任务进入她自己的活动选择并留下事实记忆，以便我私下读得更快时她能正式追上，也能阅读我指定的内容。

> 作为 Nyx，我想在探索欲出现时按主题找到真正相关的本地读物；只有匹配不到时才去探索网络，以免用“最近上传但无关”的材料假装满足探索欲。

## 验收标准

### 日程排期

- [ ] `nyx/activity/scheduler.py` 提供四个公开纯函数：

  ```python
  def desire_to_activity(desire_type: DesireType) -> ActivityType | None: ...
  def rank_desires(
      desires: list[ShortTermDesire],
      values: list[DesireValue],
  ) -> list[ShortTermDesire]: ...
  def build_schedule(
      desires: list[ShortTermDesire],
      energy: float,
      energy_delta: ActivityEnergyDelta,
  ) -> list[ActivityType]: ...
  def format_time_label(
      block_index: int,
      grid_minutes: int,
      start_hour: float,
  ) -> str: ...
  ```

- [ ] `desire_to_activity` 映射：`EXPLORATION -> READING`、`CREATION -> CREATION`、`REST -> REST`、`INTERACTION -> None`。自由探索是读书活动的运行时升级，不由该纯函数直接决定。
- [ ] `rank_desires` 按 `ShortTermDesire.strength * DesireValue.expression_weight` 降序，同分按 `created_at ASC, id ASC`；没有对应 `DesireValue` 的类型按权重 `0.0` 处理。该公式与 07-desire 容量裁剪完全相同。
- [ ] `build_schedule` 保持输入顺序，只跳过映射为 `None` 的互动欲；精力低于 `ENERGY_REST_THRESHOLD` 时，在下一项活动前插入 `REST`，直到恢复到阈值之上；`energy_delta.rest <= 0` 时跳过插入，不能死循环。
- [ ] `format_time_label` 按 `start_hour + block_index * grid_minutes / 60` 计算块起点，使用四舍五入得到分钟，返回 `"HH:MM"`。

### Facade、生命周期与恢复

- [ ] `ActivityStore` 提供 activity 与 assigned task 持久化：`insert`、`get`、`get_current`、`list_running`、`list_unfinished`、`get_paused_in_block`、`get_last_exploration`、`list_schedule`、`list_results`、`update` 以及任务 CRUD/条件领取/恢复协调。
- [ ] `ActivityLifecycle` 提供 `start(activity: Activity, is_new: bool) -> bool`、`complete(activity: Activity) -> str | None`、`fail(activity: Activity, error: str) -> str | None`、`recover_stale_running() -> list[Activity]` 与 `interrupt(activity_id: str, by_event: EventType, task: asyncio.Task[None] | None) -> str | None`；返回的可空字符串是需要提交后广播快照的关联任务 ID，`activity_goal_signal(activity: Activity) -> bool | None` 为结算辅助函数。
- [ ] `ActivityFacade` 提供：

  ```python
  async def on_tick(tick_type: TickType) -> None: ...
  async def on_desire_generated(event: Event) -> None: ...
  def select_activity(
      desires: list[ShortTermDesire],
      state: CurrentState,
  ) -> Activity | None: ...
  async def complete_activity(activity: Activity) -> None: ...
  async def interrupt(activity_id: str, by_event: EventType) -> None: ...
  async def quiesce() -> None: ...
  async def recover_stale_running() -> list[Activity]: ...
  async def get_current() -> Activity | None: ...
  async def get_schedule() -> list[Activity]: ...
  async def get_results(
      limit: int = 100,
      offset: int = 0,
      activity_type: ActivityType | None = None,
  ) -> list[Activity]: ...
  async def list_tasks() -> list[AssignedTask]: ...
  async def assign_web_task(url: str) -> AssignedTask: ...
  async def assign_book_task(
      book_id: str,
      target_paragraph: int,
  ) -> AssignedTask: ...
  ```

- [ ] `select_activity` 是同步纯决策：无欲望或全为互动欲时返回 `None`；精力不足时返回无欲望关联的 `REST`；否则返回第一个可排程欲望映射出的活动，并在 `progress` 保存 `desire_id`、`goal`、`correlation_id`、`description`。
- [ ] `SCHEDULE_BLOCK_START`、`DESIRE_GENERATED` 与新建委派任务都进入同一个 `_maybe_start_activity` 启动路径；已有活动时不重复启动。
- [ ] `get_schedule()` 以运行 Nyx 的电脑系统本地时区计算当天 `00:00`，只返回本地自然日内 `started_at` 不早于该时刻的活动，按 `started_at ASC`；不新增时区配置，不处理前后端分处不同时区。
- [ ] `get_results()` 返回跨天历史产出并按 `ended_at DESC`；`limit` / `offset` 在 SQL 层分页，`activity_type` 非空时在 SQL 层过滤，不先物化全部记录。数据库迁移为创作历史查询建立 `(status, type, ended_at DESC)` 复合索引。
- [ ] 新活动在一个本地事务中完成来源条件领取（欲望 `PENDING -> ACTIVE` 或任务 `PENDING -> RUNNING`）、activity `RUNNING` 插入和 `ACTIVITY_START` durable event 写入；任一步失败整体回滚。`PENDING` 只作为构造期与旧库恢复兼容状态，不再作为新活动的持久启动中间态。
- [ ] `activity_start`、`activity_end`、`activity_interrupted` 由活动 Facade/Lifecycle 自己发布，`source=INTERNAL`；事件优先使用活动 `progress["correlation_id"]`，缺失时回退活动 id。
- [ ] `complete_activity` 将活动、关联任务 `COMPLETED`、`goal_signal=None` 时的欲望释放和 `ACTIVITY_END` 在同一事务提交；当活动类型为 `IDLE_REFLECTION` 时，同一事务再追加普通 durable `REFLECTION(reason=idle_activity)`，不新增专用事件。执行异常把活动 `INCOMPLETE`、关联欲望 `SUPPRESSED`、关联任务 `FAILED` 与错误文本在同一事务提交，并继续抛出异常供后台 task 收割。业务事务提交后逐条 announce；announce/wake 失败不得反向把已完成活动改成 `INCOMPLETE`。
- [ ] 进程启动时，组合根在订阅事件前调用 `recover_stale_running()`：遗留 `PENDING` 一律转 `ABANDONED` 并把关联 ACTIVE 欲望释放回 `PENDING`；遗留 `RUNNING` 中 `READING`、`CREATION`、`FREE_EXPLORATION` 转 `PAUSED`，其余转 `ABANDONED`，关联欲望转 `SUPPRESSED`；不发布新的 `activity_interrupted`。
- [ ] `interrupt` 只处理存在且当前为 `RUNNING` 的目标；取消执行 task 并最多等待 5 秒，按时结束后重读活动状态，再将可续类型置 `PAUSED`，其余置 `ABANDONED`，释放关联的活动占用并发布 `activity_interrupted`。超时时保持原状态且不释放来源、不发布事件，避免仍存活的旧 runner 与新 runner 并发执行。
- [ ] 可续活动类型固定为 `READING`、`CREATION`、`FREE_EXPLORATION`。只有系统本地同一自然日且同一 `schedule_block_id` 的最近 `PAUSED` 记录可恢复，复用原 activity id；跨日期或跨日程块的旧 `PAUSED` 只留档，不自动恢复。
- [ ] `schedule_block_id(now, grid_minutes)` 先把时间戳转换为系统本地时间，再按本地小时/分钟向下对齐网格，返回 `"HH:MM"`；不得用 `now % 86400` 推导用户本地时钟。
- [ ] `quiesce()` 先关闭新活动准入，再取消当前后台 task 并最多等待 5 秒；runner 按时结束后，在共享数据库关闭前按启动恢复规则原子落定遗留活动、欲望和任务。超时 runner 保持原 `RUNNING` 状态且不重排，留给其后续自行结算或下次启动恢复；正常关停不发布伪造的用户打断事件。

### 委派任务数据模型与持久化

```python
class AssignedTaskType(StrEnum):
    WEB = "web"
    BOOK = "book"

class AssignedTaskStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"

@dataclass
class AssignedTask:
    id: str
    type: AssignedTaskType
    status: AssignedTaskStatus
    url: str | None
    book_id: str | None
    target_paragraph: int | None
    checkpoint: dict[str, Any]
    error: str | None
    created_at: float
    updated_at: float
```

- `WEB` 只设置 `url`；`BOOK` 只设置 `book_id/target_paragraph`。本轮不增加任务标题、优先级、定时、截止时间、重复规则、编辑、拖动排序或插件式任务载荷。
- `ActivityStore` 除 activity CRUD 外，还负责委派任务的创建、查询、FIFO 领取、checkpoint、状态更新和启动恢复；不新增 Repository/Service/Manager。
- 同一类型、同一目标已有 `PENDING/RUNNING` 任务时，创建操作幂等返回最早的原任务。
- `assigned_task` 表和 FIFO 索引为：

  ```sql
  CREATE TABLE assigned_task (
      id TEXT PRIMARY KEY,
      type TEXT NOT NULL,
      status TEXT NOT NULL,
      url TEXT,
      book_id TEXT REFERENCES books(id) ON DELETE SET NULL,
      target_paragraph INTEGER,
      checkpoint TEXT NOT NULL DEFAULT '{}',
      error TEXT,
      created_at REAL NOT NULL,
      updated_at REAL NOT NULL
  );

  CREATE INDEX idx_assigned_task_status_created
  ON assigned_task(status, created_at);
  ```

- 删除已安排书籍后 `book_id` 置空；执行时任务明确失败，不保留无法验证归属的目标。

### 委派任务选择与生命周期

空闲选择顺序固定为：

1. 仍在运行的内存 task / `RUNNING` 活动；
2. 同一日程块最近的 `PAUSED` 活动；
3. 有待执行任务且精力低于 `ENERGY_REST_THRESHOLD` 时的 `REST`；
4. 最早创建的 `PENDING` 委派任务；
5. 排序后的短期欲望；
6. 默认观察或发呆反思。

- 领取任务、插入 `RUNNING` activity 与 `ACTIVITY_START` 必须在同一个本地事务内完成。任务活动继续使用 `ActivityType.READING`，并在 `Activity.progress` 保存 `task_id`、任务来源、目标与 `correlation_id=task_id`；不新增任务专用 ActivityType。
- 创建任务后立即尝试 `_maybe_start_activity()`；已有活动时只入队，不抢占。
- 任务活动完成后在活动完成事务内置 `COMPLETED`；执行异常在活动失败事务内置 `FAILED` 并保存至多 500 字符的错误，不自动重试。
- 任务活动被打断时在活动中断事务内回到 `PENDING`；同日同块优先恢复原活动并重新领取，跨块则创建新的 activity 从持久化 checkpoint 继续，旧 `PAUSED` 记录只留档。
- 启动恢复按关联 activity 调和遗留 `RUNNING` 任务：活动已完成则任务补为 `COMPLETED`，活动失败则任务补为 `FAILED`，可恢复/无执行者则任务回到 `PENDING`；不得把已有完成证据的任务盲目重排执行。
- 任务状态先落库，再按 04-module-bus-system 发布 `task_updated` 广播；SSE 失败不得回滚任务状态。
- 完成、失败或中断的收尾事务失败时先保持原子回滚，再由 Facade 立即执行恢复调和；若恢复也遇到临时故障，下一次准入发现内存 runner 已结束而数据库仍为 `RUNNING` 时必须重试恢复，不能让孤立 `RUNNING` 永久阻塞队列。
- 创建任务提交成功后，`task_updated`、立即调度和调度后的状态回读都是提交后旁路；旁路失败只记录日志并返回已持久化的任务快照，不得让发布 API 返回假失败。任务插入本身失败仍正常抛错。

### 网页委派任务

- 创建时只接受带主机名的 `http` / `https` URL；`exploration.web_enabled=false` 时返回冲突且不创建任务。
- 执行只调用现有 `web_fetch`，不先搜索；继续复用逐跳公网校验、重定向限制、15 秒请求超时和 20 万字符正文上限。
- 空正文、非公网地址、下载或解析失败都使任务 `FAILED`，不得沉淀 snippet 或伪正文。
- `checkpoint` 保存 `cursor/profile/pending`。正文从 cursor 起每次最多 6000 字符调用 `MemoryFacade.digest_source_block`；先保存 pending，再写 knowledge，最后推进 cursor、采纳 profile 并清 pending。
- knowledge 使用稳定的 `web:<normalized-url>` 来源 topic；`source_name` 使用 URL。记忆写入后、cursor 推进前崩溃时允许重放，由同一来源内去重吸收。
- 网页任务读完整个已抽取正文；它不改变自动自由探索“每条搜索结果最多读取 6000 字符”的既有语义。

### Goal 结算

- [ ] `_goal_met(goal, result)` 的语义为：`goal is None -> True`；`action == "read"` 要求 `result.completed` 为真；`action == "write"` 要求 `title` 与 `content` 都存在且非空；`action == "observe"` 要求 `presence` 存在；自由探索 `outcome == "won"` 视为达成；其他情况为假。
- [ ] `activity_goal_signal(activity) -> bool | None` 优先读取 `activity.progress["goal_signal"]`：该键存在且值为 `bool` 或 `None` 时直接使用，否则按 `goal` 与 `result` 调用 `_goal_met`。
- [ ] `activity_end.goal_met is None` 表示本次有进展但不结算欲望；下游不得把它当作失败，不增加 `retry_count`。此时若欲望仍为 `ACTIVE`，活动完成逻辑将其释放回可消费状态。
- [ ] `activity_end.goal_met is True` 或 `False` 时，按 07-desire 的满足/失败规则回写 `goal_progress`、`retry_count` 与欲望状态。
- [ ] `should_explore(last_explored_at: float, rate_limit_hours: int, now: float) -> bool` 只按时间间隔判断自由探索限速，`now - last_explored_at >= rate_limit_hours * 3600` 时允许升级；探索欲与精力条件由调用方分别保证。

### 可续执行与 checkpoint

- [ ] `Activity.progress` 是状态机唯一持久化载体，本轮不新增 activity 子表，也不新增公共 runner 基类。
- [ ] 每个可续 runner 遵循：先保存安全点，再执行不可逆副作用；副作用完成后立即保存去重锚点；恢复时按锚点跳过已完成步骤；终局 finalize 和 sink 只执行一次。
- [ ] checkpoint 更新必须经过 `ActivityStore.update`；EPUB 阅读进度与原文沉淀 checkpoint 只由 12-reading-system 的 `books` / `reading_progress` 管理。

#### EPUB reading checkpoint

- [ ] 活动系统不保存第二套文件分块 checkpoint。明确委派与探索欲读书统一调用 12-reading-system 的 `read_for_activity()`，其 `reading_progress` 与 `books.memory_state` 是唯一持久恢复来源。
- [ ] 探索欲命中 EPUB 并由 `read_for_activity(..., target_paragraph=None)` 读完一块但未到
  书末时，同样设置 `goal_signal=None`；这是可续进展，不得按失败增加欲望 retry。

#### Creation checkpoint

- [ ] `activity.progress["creation"]` 形状为：

  ```json
  {
    "style": "diary",
    "llm_done": false,
    "title": null,
    "content": null,
    "file_written": false,
    "path": null
  }
  ```

- [ ] 首次执行选定创作风格并保存；恢复时复用原风格。
- [ ] LLM 结果的 `title`/`content` 必须都是去空白后非空的字符串；成功后保存两字段并置 `llm_done=true`。文件写入经注入的 `ToolRegistry.call("file_io", ...)`，成功后保存非空 `path` 并置 `file_written=true`；恢复不得重复调用 LLM，同路径同内容由 05-tools 保证效果幂等。
- [ ] 创作 prompt 注入 canon、当前情绪/精力/活动欲望、四轴审美与当前观察；按 goal topic/描述调用记忆融合召回，从相关 knowledge 和历史 creation 记忆中取最多 3 条参考，并要求输出前内部自审。输出 JSON `{title, content}`。
- [ ] 创作文件 stem 先移除非法字符、截断到 96 字符并清除尾部空格/点，再附加 activity id 的 8 位稳定哈希；路径为 `workspace/creations/<safe-title>-<activity-hash>.md`，同名作品不得互相覆盖。

#### Free exploration checkpoint

- [ ] `Exploration.run(activity) -> dict[str, Any]` 使用显式状态机，状态集合为 `searching`、`reading_results`、`summarizing`、`sinking`、`completed`。阶段异常由 Activity 生命周期落为 `INCOMPLETE`，不另造不可恢复的 exploration `failed` checkpoint。
- [ ] `activity.progress["exploration"]` 形状为：

  ```json
  {
    "state": "searching",
    "topic": "seed topic",
    "raw_results": [],
    "cursor": 0,
    "findings": [],
    "tool_calls": [],
    "source_pending": null,
    "summary_done": false,
    "judged": null,
    "sink_done": false
  }
  ```

- [ ] `searching` 调用 web 或 local search，并保存结果与工具调用；`reading_results` 从 `cursor` 继续处理最多 3 条结果，每条完成后追加 finding、记录 tool call 并推进 cursor；`summarizing` 调用 LLM 生成判断结果；`sinking` 只执行一次父长期欲望子主题追加与 knowledge 回写，不新增长期欲望、不修改其 strength；`completed` 构造最终结果。
- [ ] web 结果用 `web_fetch` 取正文；local 结果不能只使用搜索 snippet，必须用既有
  `file_io(action="read")` 读取文件。进入 finding / 原文沉淀 prompt 的单条正文最多 6000
  字符，来源分别用 URL / 绝对路径生成 `web:` / `local:` topic。
- [ ] 每条搜索结果的 `{finding, knowledge}` 先保存在 `source_pending`，再写 knowledge
  并推进 cursor；崩溃恢复复用 pending，记忆已写但 cursor 未推进时由既有去重吸收重放。
  正文读取失败才回退 snippet；正文与 snippet 都为空白时不调用原文沉淀 LLM、不生成
  finding/knowledge，但该条 cursor 仍正常推进。
- [ ] `FREE_EXPLORATION` 被打断后恢复同一 activity id，不重复抓取 cursor 之前的结果、不重复成功 tool call、不重复追加源长期欲望 subtopics 或 `remember_knowledge`。
- [ ] `web_enabled=false` 时只调用 `local_search`；联网搜索为空时可回退 local search；单条 `web_fetch` 失败记录失败 tool call 并使用 snippet，不使探索崩溃。
- [ ] 探索 LLM 调用传递活动 correlation id，`output_type="exploration_finalize"` 的输出完成后紧跟 `evaluator.evaluate`；总结 JSON 非对象时返回可序列化的空结果。
- [ ] 探索最终结果至少包含 `type`、`outcome`、`summary`、`core_discovery`、`knowledge`、`new_topics`、`strong_new_topics`、`findings`、`tools`。
- [ ] 活动从短期欲望创建时把 `parent_long_term_id` 复制进 `Activity.progress`；探索终局只把 `strong_new_topics` 作为值得继续的子主题追加到该父长期欲望。父 ID为空或父记录已删除时跳过子主题更新，知识沉淀和活动结算照常；探索不得新增长期欲望。

真实 bad case 的处理边界：

| 情况 | 处理 |
|---|---|
| web fetch / local file read 失败 | 记录失败 tool call，仅此时回退搜索 snippet |
| local search 只返回短 snippet | 使用现有 `file_io(read)` 读取真实文件，不能把 snippet 当全文 |
| 正文与 snippet 都为空白 | 跳过原文沉淀 LLM 与 finding/knowledge，完成该条 cursor 推进 |
| 单条网页或本地正文超过 6000 字符 | 本轮仅取前 6000 字符进入 finding 与沉淀，保持现有每条搜索结果一次处理边界 |
| 探索 knowledge 已写、cursor 未推进 | 重放 `source_pending`，按 `web:` / `local:` 来源内去重 |

### 活动类型执行

- [ ] `READING` 可由探索欲或委派任务产生。探索欲带非空 `goal.topic` 时，对 topic 与 EPUB
  `title/filename` 使用同一归一化：Unicode `casefold`、去扩展名、移除空白、标点和书名号。
- [ ] 统一选材先比较归一化后完全相等，再比较双向包含，最后按
  `difflib.SequenceMatcher` 相似度排序；相似度低于 `0.6` 不算匹配，同分取最近导入项。
  已完成 EPUB 不参与。
- [ ] 命中 EPUB 时经 12-reading-system 的 `read_for_activity` 窄回调执行。没有匹配时禁止
  回退到任意“最近一本”；通过 `should_explore` 限速后升级 `FREE_EXPLORATION`。网络关闭时
  沿用本地搜索配置，不调用 web 工具。没有非空 topic 时不能臆测书名，直接走既有默认活动。
  自动 EPUB 读块返回 `completed=False` 时必须显式写 `goal_signal=None`。
- [ ] `CREATION` 执行一次创作 LLM，并通过 `ToolRegistry` 写入 `workspace/creations/<safe-title>-<activity-hash>.md`。
- [ ] `IDLE_REFLECTION` runner 只返回空结果，不直接调用 `inner_life.reflect`；活动成功完成时由 lifecycle 沿传统事件路线追加 `REFLECTION`，载荷为 `{reason: "idle_activity", activity_id, evidence}`，后续统一由 `inner_life.reflection` durable consumer 复核和处理。
- [ ] `OBSERVE_USER` 读取组合根维护的 presence、窗口标题和可选 screen summary，使用 `build_observation_summary` 生成摘要，运行时不调用 LLM。
- [ ] `REST` 不调用 LLM，返回空 result。
- [ ] 空槽默认活动：无欲望或全为互动欲时，精力 `< ENERGY_REST_THRESHOLD` 选择 `IDLE_REFLECTION`，否则选择 `OBSERVE_USER`；默认活动不关联欲望。
- [ ] 精力变化按 `ActivityType.value` 从 `ActivityEnergyDelta` 同名字段读取：`reading=-20`、`creation=-25`、`free_exploration=-30`、`observe_user=-10`、`idle_reflection=+10`、`rest=+30`。

### 观察与屏幕视觉

- [ ] `classify_presence(idle_seconds: float) -> str` 是 presence 三态判定的纯函数事实来源：最后输入距今 `<30` 秒为 `"online"`，`30 <= idle_seconds < 300` 为 `"busy"`，`>=300` 秒为 `"away"`。边界分别归入后一档；窗口标题只作为观察内容，不参与 presence 判定。
- [ ] Windows Tauri `sample_presence() -> tuple[int, str]` 返回系统最后输入距今的毫秒数和真实前台窗口标题，不在 Rust 层应用 presence 阈值。WebView 每 30 秒采样一次；非 Tauri/非 Windows 降级为 WebView 内键盘/鼠标最后输入时间，窗口标题固定为空，不得使用 Nyx 自身 `document.title` 冒充前台应用。
- [ ] Rust 命令使用 `Result<(u64, String), String>` 承载错误通道；成功 JS 值仍是 `[idle_ms, title]`。原生采样失败/非 Windows 必须拒绝调用以触发 WebView 降级，不返回 `0` 伪造在线事实。
- [ ] 前端在 presence 或窗口标题变化时上报 `{presence, window_title, idle_seconds, sampled_at}`；SSE 重连后重新采样、强制建立后端基线。“已发送快照”必须在请求成功后更新，失败在后续采样重试。单次请求 10 秒超时并取消，卸载也取消；同一 effect 至多一个请求在途。
  请求在途时新采样覆盖待上报快照，只保留最新值（A/B/A 不得漏掉末次 A）；原生采样序号
  丢弃乱序完成的旧结果。非法原生 tuple/数值/标题走已有 WebView fallback，不伪造 idle=0。
  sampled_at 在调用原生采样前记录，标题截断至 512 字符。
  WebView fallback 的闲置时长用 performance.now 单调时钟计算，不受系统时间跳变影响；
  sampled_at 回拨时不去重、强制上报基线，字符截断不切开 Unicode 代理对。
  采样分辨率为 30 秒，识别离开/归来允许该级别误差；WebView 降级只能观察窗口内输入，不能宣称有系统级感知。
- [ ] 首次成功观察只建立 presence 基线，不产生归来。基线建立后进入 `away` 时，运行时以 `sampled_at - idle_seconds` 记录最后活跃起点；后续 `away -> online` 产生 `returned` 和离开时长。`busy -> online` 不算离开归来。迟到采样/用户消息不能覆盖新证据；再次 away 废弃旧归来。时钟回拨观察重建基线，不伪造归来。
- [ ] 已持久化的 `USER_MESSAGE` 是比周期采样更及时的 online 证据：runtime 在交给表达系统前，按消息事件时间把 presence 幂等对齐为 online；若此前为 away，则产生同一份一次性 returned 上下文。之后到达的 online observation 不得重复产生 returned。首次运行时事实来自用户消息时仍只建立基线，不伪造归来。
- [ ] 归来不立即强制 Nyx 发言；它作为一次性运行时上下文，交给下一次成功的回复、主动搭话或 LLM 碎碎念。表达失败、空输出或固定 fallback 不消费该上下文；进程重启后不得根据初次采样补造归来。
- [ ] `build_observation_summary` 按窗口标题优先、屏幕摘要次之拼装观察文本；无二者时返回稳定的空/默认摘要。
- [ ] `vision.enabled=true` 时，`ScreenObserver` 周期抓屏并调用 `VisionClient` 描述，失败返回 `None`；屏幕视觉只丰富观察摘要，不改变 presence 判定。
- [ ] 昼夜边界固定为本地时间 `22:00 <= time < 06:00`；本轮只影响表达上下文和前端视觉，不改变活动选择、活动能耗、情绪或精力数值。
  前后端运行于同一台电脑并使用系统本地时区，不处理远程时区分离；活动时间线也按该系统本地自然日过滤。
  日程块标签按同一系统本地时钟生成，不随表达昼夜感知改变。

## `activity_end`、REST 与 SSE 契约

- [ ] `activity_end.content` 固定为：

  ```json
  {
    "activity_id": "string",
    "type": "string",
    "desire_id": "string or null",
    "goal_met": "boolean or null",
    "energy_delta": "number",
    "result": {}
  }
  ```

- [ ] `desire_id`/`goal_met` 由 07-desire 消费，`energy_delta` 由 08-inner-life 消费，`type`/`result` 由记忆系统与前端消费；`reading` 与 `free_exploration` 结束后按 07-desire 规则给创造欲加压。
- [ ] `GET /api/activity` 返回 `{current, schedule}`；`GET /api/activity/results` 返回历史产出，接受 `limit=1..100`、`offset=0..100000` 与可选且当前仅允许 `creation` 的 `activity_type`，越界查询由 FastAPI 返回 422、不得进入 SQLite。活动系统不提供通用文件上传或 material 列表端点；书籍导入只走 12-reading-system 的 `POST /api/books`。
- [ ] 委派任务端点固定为：`GET /api/tasks` 按 `created_at DESC` 返回最近任务；`POST /api/tasks/web` 以 `{url}` 创建网页任务并返回 201；`POST /api/tasks/book` 以 `{book_id, target_paragraph}` 创建 EPUB 任务并返回 201。URL 形状错误或目标段越界返回 422，书不存在返回 404，联网关闭返回 409。
- [ ] `current`、`schedule`、`results` 继续返回现有 `Activity` dataclass；`progress` 中的 checkpoint 作为 JSON 内追加字段，不破坏旧字段。
- [ ] 活动页用“网页 / 书籍”分段控件创建任务。书籍模式复用 `GET /api/books` 和现有段落窗口 API，目标段号保持 1-based 并限制在 `[1,total_paragraphs]`，展示目标段及前后各一段预览。任务列表至少显示类型、目标、状态、创建时间和失败原因；`task_updated` 触发 activity store 重拉活动、创作和任务快照。
- [ ] 前端活动页的产出区只显示最近 12 条已完成创作的标题与本地时间，不显示正文、读书或探索产出；独立“创作”页复用同一 store，按 12 条一批加载历史创作，正文默认折叠，展开后显示完整正文与落盘路径。
- [ ] `activityStore` 为创作结果请求维护内部世代；`refresh()` 开始后，先前分页请求的成功或失败结果都必须丢弃，不得追加到或覆盖较新的首页快照、错误与 loading 状态。
- [ ] 活动生命周期 SSE 事件保持 `activity_start`、`activity_end`、`activity_interrupted`；委派任务另用 `task_updated` 快照通知。统一 payload 为 `event.content` 展开并附 `event_id`、`correlation_id`、后端 `timestamp`，公共头按 04-module-bus-system 定义。

### 委派任务 bad cases

| 情况 | 处理 |
|---|---|
| 当前活动正在运行 | 任务只入队，不抢占 |
| 精力过低 | 先安排 `REST`，任务保持 `PENDING` |
| URL 指向内网、抓取失败或正文为空 | 任务 `FAILED`，不写记忆 |
| 网页超过抓取上限 | 按既有 20 万字符正文上限阅读已抽取内容并完成 |
| 书在排队后被删除 | 任务 `FAILED`，错误对用户可见 |
| 目标段落越界 | 创建时 422；执行前再次校验，防排队期间数据变化 |
| 目标已经读过 | 幂等完成，不回退进度、不增加读完次数 |
| 活动中断或进程崩溃 | 从 checkpoint 重排或恢复，不永久停在 `RUNNING` |
| 记忆已写但 checkpoint 未推进 | 允许重放，由同一来源内去重吸收 |
| 完成/失败/中断收尾事务回滚 | 立即恢复调和；恢复仍失败时由下一次准入重试，不遗留无执行者的 `RUNNING` |
| runner 吞掉取消 | 5 秒后停止等待，保持原状态且不重排，避免双 runner |
| 任务已提交但立即调度失败 | 返回已持久化任务，保持 `PENDING` 等待后续 tick，不向用户报告发布失败 |
| 用户与后台同时保存进度 | 12-reading-system 的 revision 冲突合并保证普通路径 Nyx 位置只前进 |
| 模糊匹配到多个候选 | 最高分优先，同分取最近导入；不随机选择 |
| 主题没有本地匹配 | 跳过“读最新”，按限速和联网配置进入探索 |

## 对象完整性

### Activity

**入口清单**

| 入口 | 产生条件 | 写入位置 |
|---|---|---|
| 日程 tick / `DESIRE_GENERATED` | 空闲且存在可排程欲望或默认活动 | `activity` + `ACTIVITY_START` 同事务 |
| 委派任务 | 最早 `PENDING` 任务且精力允许 | `assigned_task` 领取 + `activity` + 事件同事务 |
| 同日同块恢复 | 存在最近 `PAUSED` 活动 | 原 `activity` 更新为 `RUNNING` |
| 启动/关闭恢复 | 无内存 task 承接遗留状态 | 原 `activity` 原子转 `PAUSED/ABANDONED` |

**消费者清单**

| 消费者 | 发现方式 | 用途 |
|---|---|---|
| Activity runner | 启动后持有 activity | 执行阅读、创作、探索、观察、反思或休息 |
| 欲望/内在生命/记忆 | durable `ACTIVITY_END` | 结算目标、精力与活动记忆 |
| REST/SSE/前端 store | current/schedule/results 与活动事件 | 展示当前、时间线及产出 |

**状态迁移表**

| 当前状态 | 条件 | 下一状态 | 副作用 / 失败落点 |
|---|---|---|---|
| 构造期 `PENDING` | 来源领取与开始事件事务成功 | `RUNNING` | 事务失败不留下 activity 或 claim |
| `RUNNING` | runner 成功 | `COMPLETED` | 任务终态与 `ACTIVITY_END` 同事务 |
| `RUNNING` | runner 异常 | `INCOMPLETE` | 欲望抑制、任务失败同事务 |
| `RUNNING` | 打断/关闭/崩溃恢复 | `PAUSED/ABANDONED` | 关联状态同事务落定 |
| `PAUSED` | 同日同块恢复 | `RUNNING` | 复用 activity id 和 checkpoint |

**Bad case 表**

| 情况 | 处理 |
|---|---|
| 空 | 无候选生成默认活动；无 topic 不臆测 EPUB |
| 失败 | 本地事务整体回滚；runner 失败进入 `INCOMPLETE` |
| 部分完成 | checkpoint 先于不可逆副作用，EPUB 进度由阅读系统单调推进；闲置活动终态与反思请求同事务，不会只落一边 |
| 乱序 | 启动锁串行选择；只恢复同日同块最新暂停项 |
| 重放 | durable consumer effect marker 防重复；runner 按 checkpoint 跳过已完成步骤 |
| 删除 | EPUB 被删除时任务可见失败；旧 material 未完成活动由迁移退役 |

### AssignedTask

**入口清单**：`POST /api/tasks/web` 与 `POST /api/tasks/book`；启动恢复只修正已有行，不创建新任务。

**消费者清单**：`ActivityStarter` FIFO 选择、网页/EPUB runner、REST 任务列表与前端 activity store。

**状态迁移表**

| 当前状态 | 条件 | 下一状态 | 副作用 / 失败落点 |
|---|---|---|---|
| `PENDING` | 与活动原子启动成功 | `RUNNING` | 同事务写 Activity 与开始事件 |
| `RUNNING` | Activity 完成/失败 | `COMPLETED/FAILED` | 与 Activity 终态同事务 |
| `RUNNING` | 打断、关闭或可恢复崩溃 | `PENDING` | checkpoint 保留 |
| `RUNNING` | 收尾事务回滚且 runner 已结束 | `PENDING` | 恢复调和把 Activity 置 `PAUSED/ABANDONED`；恢复失败由下一次准入重试 |

**Bad case 表**：空队列不建活动；失败保存 500 字符错误；部分完成保留 checkpoint；FIFO 消除选择乱序；重复创建活动任务返回已有活跃任务；书删除时失败，不改选其它书；取消超时保持 `RUNNING` 且不重排；提交后调度失败仍返回已持久化任务。

### Legacy Material

**入口清单**：不再有运行时入口；数据库升级只读取旧 `material`/Activity 行执行退役迁移。

**消费者清单**：运行时代码无消费者；历史已完成 Activity 与既有 `material:` memory 仍可展示/检索。

**状态迁移表**：旧未完成 material Activity 升级时转 `ABANDONED`，关联 `ACTIVE/SUPPRESSED` 欲望回到 `PENDING`，随后删除 `material` 表；已完成 Activity 与记忆不改写。

**Bad case 表**：空表直接删除；迁移失败整体回滚；活动与欲望在同一 migration 事务更新；迁移按 schema version 只执行一次；旧上传文件不删除；数据库行删除后无恢复入口。

## 测试要点

- [ ] EPUB 选材纯函数：文件名归一化、完全匹配、双向包含、相似度阈值、已完成过滤与同分导入时间排序。
- [ ] `tests/test_activity/test_scheduler.py`：四种欲望映射、权重排序与 FIFO、缺失值默认 0、空输入、低精力/多次休息、互动欲跳过、非正休息增量防死循环、时间标签与浮点分钟四舍五入。
- [ ] `tests/test_activity/test_activity_store.py`：activity insert/get 往返、枚举与 progress JSON、current/running/paused/schedule/results 查询、创作过滤与分页、exploration 最近时间、update；DB 测试核对活动历史复合索引。
- [ ] 委派任务 Store/API：CRUD、FIFO 原子领取、重复目标幂等、完成/失败、启动恢复、迁移索引，以及三个端点的 2xx/404/409/422。
- [ ] `tests/test_activity/test_activity_lifecycle.py`：goal 判定、`goal_signal` 覆盖、原子启动/完成/失败/打断、correlation 透传、`IDLE_REFLECTION` 完成时同事务追加普通 durable `REFLECTION`、启动/关闭恢复与任务/欲望状态回写。
- [ ] `tests/test_activity/test_activity_facade.py`：空槽默认、欲望映射、精力休息、后台启动、idle runner 不直调反思、`activity_end` content、读书部分进展的 `goal_met=None`、完整读书满足、创作 checkpoint 恢复、主题召回与历史创作参考、同日同块恢复、跨日/跨块不恢复。
- [ ] `tests/test_activity/test_llm_result.py` / `test_activity_paths.py`：活动结果非空字符串校验、未知输出类型拒绝、安全文件名长度和同名创作唯一路径。
- [ ] `tests/test_activity/test_exploration.py`：所有探索阶段 checkpoint、local/web 搜索分支、fetch 失败兜底、cursor 恢复、summary 评估、sink 去重、最终结果结构。
- [ ] 委派与选材集成：任务不抢占、暂停优先、低精力休息、任务优先欲望、网页多块 6000 字符与 pending 恢复、稳定来源 topic、空正文失败、禁网拒绝、EPUB-only 排序、无匹配不读最新并升级搜索。
- [ ] 收尾与发布故障：完成/失败/中断事务失败后调和孤立 `RUNNING`，首次恢复失败后下一次准入重试；取消超时有界返回且不重排；任务提交后的调度/回读失败仍返回成功。
- [ ] EPUB 自动分块未读完时 `activity_end.goal_met=None` 且释放 ACTIVE 欲望；探索结果
  正文与 snippet 都为空白时不调用 `digest_source_block`。
- [ ] 前端活动页：网页/EPUB 表单、目标段前后预览、任务状态/失败原因和 `task_updated` 刷新。
- [ ] `tests/test_activity/test_observe.py`：presence 的 30 秒/5 分钟边界、窗口标题不参与判定，以及观察摘要四种组合。
- [ ] `tests/test_activity/test_screen.py`：抓屏/视觉描述成功路径及 best-effort 失败路径。
- [ ] 前端 presence 测试：首次采样非归来、非空窗口标题仍可在 5 分钟后 away、失败 POST 会重试、旧请求不会覆盖新快照、`away -> online` 携带离开时长。
- [ ] runtime 测试：away 后用户立即发消息时，回复前已经得到归来上下文；随后 online observation 和 USER_MESSAGE delivery 重放都不重复归来。
- [ ] LLM、工具、文件系统、观察、评估均可注入 fake；测试验证数据流、状态与副作用次数，不验证 LLM 文本质量。

## 完成定义

- [ ] `ruff check` 零报错
- [ ] `pyright` 零报错
- [ ] `pytest` 全绿
- [ ] `npm test` 与 `npm run build` 全绿
- [ ] `docs/test-inventory.md` 已与当前测试源码同步
- [ ] `docs/facts/activity-system-facts.md` 只保留无关代码所需的实现事实，并指向本文件
- [ ] `07-desire`、`08-inner-life`、`04-module-bus-system` 与本文件的公开签名和事件契约一致；`docs/tech-reference.md` 仅更新源码索引
- [ ] `AGENTS.md` 与 `CLAUDE.md` 指向本文件作为活动系统唯一完整契约
