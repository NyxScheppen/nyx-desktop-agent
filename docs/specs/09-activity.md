# 活动系统：日程排期、委派任务与状态机

> 本文件是活动系统的唯一完整契约。它合并日程排期、活动 Facade、活动生命周期、委派任务、统一阅读选材、可续活动状态机、自由探索、观察与屏幕视觉的规格。
> `docs/facts/activity-system-facts.md` 只是供无关代码查阅的事实摘要，不替代本文件；摘要与本文件不一致时，以本文件为准，并同步修正摘要。
> spec 只定义契约（签名、语义与决策），不内联完整代码；实现以 `nyx/activity/` 源文件为准。

## 元信息

- **前置依赖**：01-types（`Activity` / `ActivityType` / `ActivityStatus` / `AssignedTask` / `AssignedTaskType` / `AssignedTaskStatus` / `DesireType` / `ShortTermDesire` / `DesireValue` / `CurrentState` / `Event` / `EventType` / `Material`）、02-config（`ActivityConfig` / `ExplorationConfig` / `ActivityEnergyDelta`）、03-llm（`LlmClient.complete` / `VisionClient`）、04-module-bus-system（`activity` / `material` 表、`EventBus` / `internal_event` / tick 路由、组合根、REST、SSE）、05-tools（`ToolRegistry`）、06-memory-system（活动/知识记忆落库）、07-desire（待消费欲望、活动状态接线、满足回写、长期欲望入口）、08-inner-life（状态快照、反思、精力变化）、10-eval（`Evaluator`）、12-reading-system（EPUB 读取入口）
- **实现文件**：`nyx/activity/scheduler.py`、`nyx/activity/store.py`、`nyx/activity/material_store.py`、`nyx/activity/starter.py`、`nyx/activity/lifecycle.py`、`nyx/activity/facade.py`、`nyx/activity/reading_runner.py`、`nyx/activity/creation.py`、`nyx/activity/llm_result.py`、`nyx/activity/paths.py`、`nyx/activity/exploration.py`、`nyx/activity/observe.py`、`nyx/activity/screen.py`、`nyx/db.py`、`nyx/api/routes.py`、`nyx/app_context.py`、`frontend/src/api/client.ts`、`frontend/src/stores/activityStore.ts`、`frontend/src/components/panels/ActivityPanel.tsx`、`frontend/src/types/api.ts`

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
- [ ] `rank_desires` 按类型级 `expression_weight` 降序，同权按 `created_at` 升序；没有对应 `DesireValue` 的类型按 `0.0` 处理。
- [ ] `build_schedule` 保持输入顺序，只跳过映射为 `None` 的互动欲；精力低于 `ENERGY_REST_THRESHOLD` 时，在下一项活动前插入 `REST`，直到恢复到阈值之上；`energy_delta.rest <= 0` 时跳过插入，不能死循环。
- [ ] `format_time_label` 按 `start_hour + block_index * grid_minutes / 60` 计算块起点，使用四舍五入得到分钟，返回 `"HH:MM"`。

### Facade、生命周期与恢复

- [ ] `ActivityStore` 提供 activity 表 CRUD：`insert`、`get`、`get_current`、`list_running`、`list_unfinished`、`get_paused_in_block`、`get_last_exploration`、`list_schedule`、`list_results`、`update`。
- [ ] `MaterialStore` 提供 material 表 CRUD：`upsert`、`next_readable`、`find_by_topic`、`get_by_path`、`advance`、`append_fragment`、`get_fragments`、`list_all`。
- [ ] `ActivityLifecycle` 提供 `recover_stale_running() -> list[Activity]` 与 `interrupt(activity_id: str, by_event: EventType, task: asyncio.Task[None] | None) -> None`；`activity_goal_signal(activity: Activity) -> bool | None` 为其结算辅助函数。
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
  async def recover_stale_running() -> list[Activity]: ...
  async def get_current() -> Activity | None: ...
  async def get_schedule() -> list[Activity]: ...
  async def get_results(
      limit: int = 100,
      offset: int = 0,
      activity_type: ActivityType | None = None,
  ) -> list[Activity]: ...
  async def list_materials() -> list[Material]: ...
  async def register_material(
      path: str,
      filename: str,
      total_chars: int,
  ) -> None: ...
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
- [ ] 有关联欲望的活动先在同一个本地事务中执行 `claim_for_activity(desire_id)`（`PENDING -> ACTIVE`）和活动 `PENDING` 插入；领取失败不创建活动，插入失败回滚领取。后台 task 开始后转 `RUNNING`；活动执行不阻塞 EventBus。
- [ ] `activity_start`、`activity_end`、`activity_interrupted` 由活动 Facade/Lifecycle 自己发布，`source=INTERNAL`；事件优先使用活动 `progress["correlation_id"]`，缺失时回退活动 id。
- [ ] `complete_activity` 将活动置为 `COMPLETED`、写入 `ended_at`，再发布 `activity_end`。执行异常将活动置为 `INCOMPLETE`、写入 `ended_at`、释放/抑制关联欲望并继续抛出异常供后台 task 收割。业务事务提交后 announce/wake 失败不得反向把已完成活动改成 `INCOMPLETE`。
- [ ] 进程启动时，组合根在订阅事件前调用 `recover_stale_running()`：遗留 `PENDING` 一律转 `ABANDONED` 并把关联 ACTIVE 欲望释放回 `PENDING`；遗留 `RUNNING` 中 `READING`、`CREATION`、`FREE_EXPLORATION` 转 `PAUSED`，其余转 `ABANDONED`，关联欲望转 `SUPPRESSED`；不发布新的 `activity_interrupted`。
- [ ] `interrupt` 只处理存在且当前为 `RUNNING` 的目标；取消并等待执行 task 后重读活动状态，再将可续类型置 `PAUSED`，其余置 `ABANDONED`，释放关联的活动占用并发布 `activity_interrupted`。
- [ ] 可续活动类型固定为 `READING`、`CREATION`、`FREE_EXPLORATION`。同一 `schedule_block_id` 内优先恢复最近的 `PAUSED` 记录，复用原 activity id；跨日程块的旧 `PAUSED` 只留档，不自动恢复。

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

- 领取任务与插入 `PENDING` activity 必须在同一个本地事务内完成。任务活动继续使用 `ActivityType.READING`，并在 `Activity.progress` 保存 `task_id`、任务来源、目标与 `correlation_id=task_id`；不新增任务专用 ActivityType。
- 创建任务后立即尝试 `_maybe_start_activity()`；已有活动时只入队，不抢占。
- 任务活动完成后置 `COMPLETED`；执行异常置 `FAILED` 并保存至多 500 字符的错误，不自动重试。
- 任务活动被打断时任务回到 `PENDING`；同块优先恢复原活动并重新领取，跨块则创建新的 activity 从持久化 checkpoint 继续，旧 `PAUSED` 记录只留档。
- 启动恢复先执行既有 activity 恢复，再把没有活跃执行者的 `RUNNING` 任务重置为 `PENDING`；不得让进程崩溃永久卡住队列。
- 任务状态先落库，再按 04-module-bus-system 发布 `task_updated` 广播；SSE 失败不得回滚任务状态。

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
- [ ] checkpoint 更新必须经过 `ActivityStore.update`；缺少新 checkpoint 时兼容从既有 activity/material 进度继续。

#### Reading checkpoint

- [ ] `activity.progress["reading"]` 形状为：

  ```json
  {
    "source": "absolute path",
    "read_from": 0,
    "read_to": 6000,
    "fragment_committed": false,
    "advanced_to": 0,
    "finalized": false,
    "note_path": null,
    "book": null,
    "note": null,
    "final_note": null
  }
  ```

  `material.memory_state` 另持久化跨 activity 的原文沉淀状态：

  ```json
  {
    "processed_to": 0,
    "profile": {"summary": "", "themes": [], "content_category": "unknown"},
    "pending": null
  }
  ```

- [ ] `ReadingActivityRunner.run(activity, source)` 只读取真实文件的 `[read_chars, read_chars + 6000)`，缺少 `source` 直接失败，禁止让 LLM 凭空编造读书内容。
- [ ] 单块 LLM 结果先保存 `book`/`note`；随后以本次至多 6000 字符原文更新滚动 profile
  并提取知识。提取结果先写入 `material.memory_state.pending`，再调用
  `remember_knowledge`，成功后推进 `processed_to`、采纳 profile 并清空 pending；最后才追加
  note fragment 和推进 material `read_chars`。恢复时复用 pending，不重复原文提取 LLM。
- [ ] 未读完整本但成功读完一块时返回 `completed=False`、实际 `read_chars` 与 `total_chars`，并设置 `goal_signal=None`。
- [ ] 探索欲命中 EPUB 并由 `read_for_activity(..., target_paragraph=None)` 读完一块但未到
  书末时，同样设置 `goal_signal=None`；这是可续进展，不得按失败增加欲望 retry。
- [ ] 读到文件末尾时只执行一次 finalize：聚合片段得到 `final_note`，写入
  `notes/<safe-filename>-<path-hash>.md`，保存 `note_path`/`finalized=true`；知识已随每块
  沉淀，不再在整本完成后重扫全文。
- [ ] 中间片段笔记使用 `note`，整本聚合笔记使用 `final_note`；恢复 finalize 必须返回终局笔记，不得把片段笔记当成完整笔记。
- [ ] 每块最多沉淀 5 条 `{topic, content, source_topic, source_name}`，来源 topic 按材料绝对
  路径稳定生成。最后不足 6000 字符的块照常沉淀。提取结构非法或记忆写入失败时不得推进
  `processed_to` / `read_chars`；沿用现有活动失败状态，下一次读同一 material 时重试，
  不把未沉淀的原文静默标成已读。
- [ ] 崩溃发生在提取后、记忆写入前时恢复复用 pending；发生在记忆写入后、checkpoint
  推进前时允许重放 `remember_knowledge`，由既有 kind-scoped 去重保证不新增重复记忆。

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

- [ ] `Exploration.run(activity) -> dict[str, Any]` 使用显式状态机，状态集合为 `searching`、`reading_results`、`summarizing`、`sinking`、`completed`、`failed`。
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

- [ ] `searching` 调用 web 或 local search，并保存结果与工具调用；`reading_results` 从 `cursor` 继续处理最多 3 条结果，每条完成后追加 finding、记录 tool call 并推进 cursor；`summarizing` 调用 LLM 生成判断结果；`sinking` 只执行一次长期欲望与 knowledge 回写；`completed` 构造最终结果。
- [ ] web 结果用 `web_fetch` 取正文；local 结果不能只使用搜索 snippet，必须用既有
  `file_io(action="read")` 读取文件。进入 finding / 原文沉淀 prompt 的单条正文最多 6000
  字符，来源分别用 URL / 绝对路径生成 `web:` / `local:` topic。
- [ ] 每条搜索结果的 `{finding, knowledge}` 先保存在 `source_pending`，再写 knowledge
  并推进 cursor；崩溃恢复复用 pending，记忆已写但 cursor 未推进时由既有去重吸收重放。
  正文读取失败才回退 snippet；正文与 snippet 都为空白时不调用原文沉淀 LLM、不生成
  finding/knowledge，但该条 cursor 仍正常推进。
- [ ] `FREE_EXPLORATION` 被打断后恢复同一 activity id，不重复抓取 cursor 之前的结果、不重复成功 tool call、不重复 `add_long_term` 或 `remember_knowledge`。
- [ ] `web_enabled=false` 时只调用 `local_search`；联网搜索为空时可回退 local search；单条 `web_fetch` 失败记录失败 tool call 并使用 snippet，不使探索崩溃。
- [ ] 探索 LLM 调用传递活动 correlation id，`output_type="exploration_finalize"` 的输出完成后紧跟 `evaluator.evaluate`；总结 JSON 非对象时返回可序列化的空结果。
- [ ] 探索最终结果至少包含 `type`、`outcome`、`summary`、`core_discovery`、`knowledge`、`new_topics`、`strong_new_topics`、`findings`、`tools`。

真实 bad case 的处理边界：

| 情况 | 处理 |
|---|---|
| 材料原文提取或 knowledge 写入失败 | 不推进 `processed_to` / `read_chars`，活动按既有失败语义结束，后续续读重试 |
| 材料 knowledge 已写、checkpoint 未推进 | 重放 pending，由同一 `material:` 来源内去重吸收 |
| web fetch / local file read 失败 | 记录失败 tool call，仅此时回退搜索 snippet |
| local search 只返回短 snippet | 使用现有 `file_io(read)` 读取真实文件，不能把 snippet 当全文 |
| 正文与 snippet 都为空白 | 跳过原文沉淀 LLM 与 finding/knowledge，完成该条 cursor 推进 |
| 单条网页或本地正文超过 6000 字符 | 本轮仅取前 6000 字符进入 finding 与沉淀，保持现有每条搜索结果一次处理边界 |
| 探索 knowledge 已写、cursor 未推进 | 重放 `source_pending`，按 `web:` / `local:` 来源内去重 |

### 活动类型执行

- [ ] `READING` 可由探索欲或委派任务产生。探索欲带非空 `goal.topic` 时，对 topic、EPUB
  `title/filename` 与 material `filename` 使用同一归一化：Unicode `casefold`、去扩展名、
  移除空白、标点和书名号。
- [ ] 统一选材先比较归一化后完全相等，再比较双向包含，最后按
  `difflib.SequenceMatcher` 相似度排序；相似度低于 `0.6` 不算匹配，同分取最近导入项。
  EPUB 与 material 一起排序，已完成候选不参与。
- [ ] 命中 EPUB 时经 12-reading-system 的 `read_for_activity` 窄回调执行；命中 material
  时沿用 `ReadingActivityRunner`。没有匹配时禁止调用 `next_readable()` 或读取最近上传材料；
  通过 `should_explore` 限速后升级 `FREE_EXPLORATION`。网络关闭时沿用本地搜索配置，不调用
  web 工具。没有非空 topic 时不能臆测书名，直接走既有默认活动。自动 EPUB 读块返回
  `completed=False` 时必须显式写 `goal_signal=None`，与 material 分块结算一致。
- [ ] `CREATION` 执行一次创作 LLM，并通过 `ToolRegistry` 写入 `workspace/creations/<safe-title>-<activity-hash>.md`。
- [ ] `IDLE_REFLECTION` 调用组合根注入的 `inner_life.reflect`，不自行发布 `REFLECTION` 事件，并把反思摘要放进结果。
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
  日程块标签的既有计时语义保持不变，不随表达昼夜感知改变。

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
- [ ] `GET /api/activity` 返回 `{current, schedule}`；`GET /api/activity/results` 返回历史产出，接受 `limit=1..100`、`offset=0..100000` 与可选且当前仅允许 `creation` 的 `activity_type`，越界查询由 FastAPI 返回 422、不得进入 SQLite；`POST /api/upload` 只注册 material，`GET /api/materials` 返回书库进度。
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
| 用户与后台同时保存进度 | 12-reading-system 的 revision 冲突合并保证普通路径 Nyx 位置只前进 |
| 模糊匹配到多个候选 | 最高分优先，同分取最近导入；不随机选择 |
| 主题没有本地匹配 | 跳过“读最新”，按限速和联网配置进入探索 |

## 测试要点

- [ ] 统一选材纯函数：文件名归一化、完全匹配、双向包含、相似度阈值、已完成过滤与 EPUB/material 跨库排序。
- [ ] `tests/test_activity/test_scheduler.py`：四种欲望映射、权重排序与 FIFO、缺失值默认 0、空输入、低精力/多次休息、互动欲跳过、非正休息增量防死循环、时间标签与浮点分钟四舍五入。
- [ ] `tests/test_activity/test_activity_store.py`：activity insert/get 往返、枚举与 progress JSON、current/running/paused/schedule/results 查询、创作过滤与分页、exploration 最近时间、update；DB 测试核对活动历史复合索引。
- [ ] 委派任务 Store/API：CRUD、FIFO 原子领取、重复目标幂等、完成/失败、启动恢复、迁移索引，以及三个端点的 2xx/404/409/422。
- [ ] `tests/test_activity/test_material_store.py`：material upsert、按 path 读取最新进度、next readable、topic 选择、fragment 追加与读取。
- [ ] `tests/test_activity/test_activity_lifecycle.py`：goal 判定、`goal_signal` 覆盖、启动/完成/失败/打断事件、correlation 透传、启动清理与欲望状态回写。
- [ ] `tests/test_activity/test_activity_facade.py`：空槽默认、欲望映射、精力休息、后台启动、`activity_end` content、读书部分进展的 `goal_met=None`、完整读书满足、创作 checkpoint 恢复、注册表落盘、主题召回与历史创作参考、同块恢复与跨块不恢复、读书知识提取。
- [ ] `tests/test_activity/test_llm_result.py` / `test_activity_paths.py`：活动结果非空字符串校验、未知输出类型拒绝、安全文件名长度和同名创作唯一路径。
- [ ] `tests/test_activity/test_reading_runner.py`：分块读取、fragment/advance 去重、每块知识与
  profile、pending 恢复、写入后重放去重锚点、末块 flush、恢复返回 `final_note`。
- [ ] `tests/test_activity/test_exploration.py`：所有探索阶段 checkpoint、local/web 搜索分支、fetch 失败兜底、cursor 恢复、summary 评估、sink 去重、最终结果结构。
- [ ] 委派与选材集成：任务不抢占、暂停优先、低精力休息、任务优先欲望、网页多块 6000 字符与 pending 恢复、稳定来源 topic、空正文失败、禁网拒绝、EPUB/material 跨库排序、无匹配不读最新并升级搜索。
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
