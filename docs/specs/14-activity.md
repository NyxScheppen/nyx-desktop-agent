# 活动系统：日程排期、活动执行与状态机

> 本文件是活动系统的唯一完整契约。它合并日程排期、活动 Facade、活动生命周期、可续活动状态机、自由探索、观察与屏幕视觉的规格。
> `docs/activity-system-facts.md` 只是供无关代码查阅的事实摘要，不替代本文件；摘要与本文件不一致时，以本文件为准，并同步修正摘要。
> spec 只定义契约（签名、语义与决策），不内联完整代码；实现以 `nyx/activity/` 源文件为准。

## 元信息

- **前置依赖**：01-types（`Activity` / `ActivityType` / `ActivityStatus` / `DesireType` / `ShortTermDesire` / `DesireValue` / `CurrentState` / `Event` / `EventType` / `Material`）、02-config（`ActivityConfig` / `ExplorationConfig` / `ActivityEnergyDelta`）、03-llm（`LlmClient.complete` / `VisionClient`）、05-module-bus-system（`activity` / `material` 表、`EventBus` / `internal_event` / tick 路由、组合根、REST、SSE）、06-tools（`ToolRegistry`）、07-memory-system（活动/知识记忆落库）、11-desire（待消费欲望、活动状态接线、满足回写、长期欲望入口）、12-inner-life（状态快照、反思、精力变化）、eval（`Evaluator`）
- **实现文件**：`nyx/activity/scheduler.py`、`nyx/activity/store.py`、`nyx/activity/material_store.py`、`nyx/activity/starter.py`、`nyx/activity/lifecycle.py`、`nyx/activity/facade.py`、`nyx/activity/reading_runner.py`、`nyx/activity/creation.py`、`nyx/activity/llm_result.py`、`nyx/activity/paths.py`、`nyx/activity/exploration.py`、`nyx/activity/observe.py`、`nyx/activity/screen.py`

## 用户故事

> 作为 Nyx 系统的开发者，我想让活动系统把欲望从「生成」推进到「消费、执行、结算或恢复」，并把活动进度、打断原因和产出广播给前端，以便欲望闭环、活动时间线和可续工作都能稳定运行。

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

- [ ] `ActivityStore` 提供 activity 表 CRUD：`insert`、`get`、`get_current`、`list_running`、`get_paused_in_block`、`get_last_exploration`、`list_schedule`、`list_results`、`update`。
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
  async def get_results(limit: int = 100) -> list[Activity]: ...
  async def list_materials() -> list[Material]: ...
  async def register_material(
      path: str,
      filename: str,
      total_chars: int,
  ) -> None: ...
  ```

- [ ] `select_activity` 是同步纯决策：无欲望或全为互动欲时返回 `None`；精力不足时返回无欲望关联的 `REST`；否则返回第一个可排程欲望映射出的活动，并在 `progress` 保存 `desire_id`、`goal`、`correlation_id`、`description`。
- [ ] `SCHEDULE_BLOCK_START` 与 `DESIRE_GENERATED` 都进入同一个 `_maybe_start_activity` 启动路径；已有活动时不重复启动。
- [ ] 活动先以 `PENDING` 插入，后台 task 开始后转 `RUNNING`；活动执行不阻塞 EventBus。
- [ ] `activity_start`、`activity_end`、`activity_interrupted` 由活动 Facade/Lifecycle 自己发布，`source=INTERNAL`；事件优先使用活动 `progress["correlation_id"]`，缺失时回退活动 id。
- [ ] `complete_activity` 将活动置为 `COMPLETED`、写入 `ended_at`，再发布 `activity_end`。执行异常将活动置为 `INCOMPLETE`、写入 `ended_at`、释放/抑制关联欲望并继续抛出异常供后台 task 收割。
- [ ] 进程启动时，组合根在订阅事件前调用 `recover_stale_running()`：`READING`、`CREATION`、`FREE_EXPLORATION` 的遗留 `RUNNING` 转 `PAUSED`；`OBSERVE_USER`、`IDLE_REFLECTION`、`REST` 转 `ABANDONED`；关联欲望转 `SUPPRESSED`；不发布新的 `activity_interrupted`。
- [ ] `interrupt` 只处理存在且当前为 `RUNNING` 的目标；取消并等待执行 task 后重读活动状态，再将可续类型置 `PAUSED`，其余置 `ABANDONED`，释放关联的活动占用并发布 `activity_interrupted`。
- [ ] 可续活动类型固定为 `READING`、`CREATION`、`FREE_EXPLORATION`。同一 `schedule_block_id` 内优先恢复最近的 `PAUSED` 记录，复用原 activity id；跨日程块的旧 `PAUSED` 只留档，不自动恢复。

### Goal 结算

- [ ] `_goal_met(goal, result)` 的语义为：`goal is None -> True`；`action == "read"` 要求 `result.completed` 为真；`action == "write"` 要求 `title` 与 `content` 都存在且非空；`action == "observe"` 要求 `presence` 存在；自由探索 `outcome == "won"` 视为达成；其他情况为假。
- [ ] `activity_goal_signal(activity) -> bool | None` 优先读取 `activity.progress["goal_signal"]`：该键存在且值为 `bool` 或 `None` 时直接使用，否则按 `goal` 与 `result` 调用 `_goal_met`。
- [ ] `activity_end.goal_met is None` 表示本次有进展但不结算欲望；下游不得把它当作失败，不增加 `retry_count`。此时若欲望仍为 `ACTIVE`，活动完成逻辑将其释放回可消费状态。
- [ ] `activity_end.goal_met is True` 或 `False` 时，按 11-desire 的满足/失败规则回写 `goal_progress`、`retry_count` 与欲望状态。
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
    "knowledge_extracted": false,
    "book": null,
    "note": null,
    "final_note": null
  }
  ```

- [ ] `ReadingActivityRunner.run(activity, source)` 只读取真实文件的 `[read_chars, read_chars + 6000)`，缺少 `source` 直接失败，禁止让 LLM 凭空编造读书内容。
- [ ] 单块 LLM 结果先保存 `book`/`note`，再至多一次追加 note fragment、至多一次推进 material `read_chars`；恢复时跳过已经提交或推进的副作用。
- [ ] 未读完整本但成功读完一块时返回 `completed=False`、实际 `read_chars` 与 `total_chars`，并设置 `goal_signal=None`。
- [ ] 读到文件末尾时只执行一次 finalize：聚合片段得到 `final_note`，写入 `notes/<safe-filename>-<path-hash>.md`，保存 `note_path`/`finalized=true`，再只执行一次 knowledge 提取。
- [ ] 中间片段笔记使用 `note`，整本聚合笔记使用 `final_note`；恢复 finalize 必须返回终局笔记，不得把片段笔记当成完整笔记。
- [ ] knowledge 提取最多处理 16 个 6000 字块、最多沉淀 5 条去重后的 `{topic, content}`；单次提取或入库失败为 best-effort，不阻塞读书活动完成。

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
- [ ] LLM 成功后保存 `title`/`content` 并置 `llm_done=true`；文件写入成功后保存 `path` 并置 `file_written=true`；恢复不得重复调用 LLM 或重复写同一文件。
- [ ] 创作 prompt 注入 canon、当前情绪/精力/活动欲望，并可注入最多 3 条 knowledge 与当前观察；输出为 JSON `{title, content}`，文件名经过安全清洗。

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
    "summary_done": false,
    "judged": null,
    "sink_done": false
  }
  ```

- [ ] `searching` 调用 web 或 local search，并保存结果与工具调用；`reading_results` 从 `cursor` 继续处理最多 3 条结果，每条完成后追加 finding、记录 tool call 并推进 cursor；`summarizing` 调用 LLM 生成判断结果；`sinking` 只执行一次长期欲望与 knowledge 回写；`completed` 构造最终结果。
- [ ] `FREE_EXPLORATION` 被打断后恢复同一 activity id，不重复抓取 cursor 之前的结果、不重复成功 tool call、不重复 `add_long_term` 或 `remember_knowledge`。
- [ ] `web_enabled=false` 时只调用 `local_search`；联网搜索为空时可回退 local search；单条 `web_fetch` 失败记录失败 tool call 并使用 snippet，不使探索崩溃。
- [ ] 探索 LLM 调用传递活动 correlation id，`output_type="exploration_finalize"` 的输出完成后紧跟 `evaluator.evaluate`；总结 JSON 非对象时返回可序列化的空结果。
- [ ] 探索最终结果至少包含 `type`、`outcome`、`summary`、`core_discovery`、`knowledge`、`new_topics`、`strong_new_topics`、`findings`、`tools`。

### 活动类型执行

- [ ] `READING` 由探索欲映射而来。启动时先按 `goal.topic` 选择 material，再按最近未读完 material 续读；无 source 不创建/不执行读书活动。无可读 material 且 topic 非空、通过 `should_explore` 限速时升级为 `FREE_EXPLORATION`，否则回退默认活动。
- [ ] `CREATION` 执行一次创作 LLM 并写入 `workspace/creations/<safe-title>.md`。
- [ ] `IDLE_REFLECTION` 调用组合根注入的 `inner_life.reflect`，不自行发布 `REFLECTION` 事件，并把反思摘要放进结果。
- [ ] `OBSERVE_USER` 读取组合根维护的 presence、窗口标题和可选 screen summary，使用 `build_observation_summary` 生成摘要，运行时不调用 LLM。
- [ ] `REST` 不调用 LLM，返回空 result。
- [ ] 空槽默认活动：无欲望或全为互动欲时，精力 `< ENERGY_REST_THRESHOLD` 选择 `IDLE_REFLECTION`，否则选择 `OBSERVE_USER`；默认活动不关联欲望。
- [ ] 精力变化按 `ActivityType.value` 从 `ActivityEnergyDelta` 同名字段读取：`reading=-20`、`creation=-25`、`free_exploration=-30`、`observe_user=-10`、`idle_reflection=+10`、`rest=+30`。

### 观察与屏幕视觉

- [ ] `classify_presence` 是 presence 三态判定的唯一事实来源：活跃输入为 `"online"`，无活跃但有窗口标题为 `"busy"`，两者都无为 `"away"`。采集由前端 Tauri ingress 完成，后端接收判定结果。
- [ ] `build_observation_summary` 按窗口标题优先、屏幕摘要次之拼装观察文本；无二者时返回稳定的空/默认摘要。
- [ ] `vision.enabled=true` 时，`ScreenObserver` 周期抓屏并调用 `VisionClient` 描述，失败返回 `None`；屏幕视觉只丰富观察摘要，不改变 presence 判定。

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

- [ ] `desire_id`/`goal_met` 由 11-desire 消费，`energy_delta` 由 12-inner-life 消费，`type`/`result` 由记忆系统与前端消费；`reading` 与 `free_exploration` 结束后按 11-desire 规则给创造欲加压。
- [ ] REST 路径保持不变：`GET /api/activity` 返回 `{current, schedule}`，`GET /api/activity/results` 返回历史产出，`POST /api/upload` 只注册 material，`GET /api/materials` 返回书库进度。
- [ ] `current`、`schedule`、`results` 继续返回现有 `Activity` dataclass；`progress` 中的 checkpoint 作为 JSON 内追加字段，不破坏旧字段。
- [ ] SSE 事件类型保持 `activity_start`、`activity_end`、`activity_interrupted`；统一 payload 为 `event.content` 展开并附 `event_id`、`correlation_id`。

## 测试要点

- [ ] `tests/test_activity/test_scheduler.py`：四种欲望映射、权重排序与 FIFO、缺失值默认 0、空输入、低精力/多次休息、互动欲跳过、非正休息增量防死循环、时间标签与浮点分钟四舍五入。
- [ ] `tests/test_activity/test_activity_store.py`：activity insert/get 往返、枚举与 progress JSON、current/running/paused/schedule/results 查询、exploration 最近时间、update。
- [ ] `tests/test_activity/test_material_store.py`：material upsert、按 path 读取最新进度、next readable、topic 选择、fragment 追加与读取。
- [ ] `tests/test_activity/test_activity_lifecycle.py`：goal 判定、`goal_signal` 覆盖、启动/完成/失败/打断事件、correlation 透传、启动清理与欲望状态回写。
- [ ] `tests/test_activity/test_activity_facade.py`：空槽默认、欲望映射、精力休息、后台启动、`activity_end` content、读书部分进展的 `goal_met=None`、完整读书满足、创作 checkpoint 恢复、同块恢复与跨块不恢复、读书知识提取。
- [ ] `tests/test_activity/test_reading_runner.py`：分块读取、fragment/advance 去重、终局笔记与 knowledge finalize 去重、恢复返回 `final_note`。
- [ ] `tests/test_activity/test_exploration.py`：所有探索阶段 checkpoint、local/web 搜索分支、fetch 失败兜底、cursor 恢复、summary 评估、sink 去重、最终结果结构。
- [ ] `tests/test_activity/test_observe.py`：presence 三态和观察摘要四种组合。
- [ ] `tests/test_activity/test_screen.py`：抓屏/视觉描述成功路径及 best-effort 失败路径。
- [ ] LLM、工具、文件系统、观察、评估均可注入 fake；测试验证数据流、状态与副作用次数，不验证 LLM 文本质量。

## 完成定义

- [ ] `ruff check` 零报错
- [ ] `pyright` 零报错
- [ ] `pytest` 全绿
- [ ] `docs/activity-system-facts.md` 只保留无关代码所需的实现事实，并指向本文件
- [ ] `11-desire`、`12-inner-life`、`05-module-bus-system` 与本文件的公开签名和事件契约一致；`docs/tech-reference.md` 仅更新源码索引
- [ ] `AGENTS.md` 与 `CLAUDE.md` 指向本文件作为活动系统唯一完整契约
