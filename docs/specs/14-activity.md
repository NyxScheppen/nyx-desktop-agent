# ActivityFacade + 行为链 + 观察

> 范围：`activity/store.py`（ActivityStore，新增）+ `activity/material_store.py`（MaterialStore，新增）+ `activity/facade.py`（ActivityFacade）+ `activity/exploration.py`（联网探索，线性）+ `activity/observe.py`（观察用户判定）+ `activity/screen.py`（屏幕视觉，opt-in）。
> 活动系统是「欲望的消费端」（design §1.3）：把 `DesireFacade.get_pending()` 的欲望映射成日程块活动、执行、判定 goal、发布 `activity_end` 让 desire/inner_life 消费回写。13-activity-scheduler 的四个纯函数（`desire_to_activity` / `rank_desires` / `build_schedule` / `format_time_label`）是本 spec 的决策底座。
> spec 只定义契约（签名 + 活动执行/探索/观察语义 + 阈值决策）；实现以 `nyx/activity/store.py` / `nyx/activity/material_store.py` / `nyx/activity/facade.py` / `nyx/activity/exploration.py` / `nyx/activity/observe.py` / `nyx/activity/screen.py` 源文件为准。

## 元信息

- **前置依赖**：05-event（`EventBus` / ROUTING）、06-tools（`ToolRegistry`）、11-desire（`DesireFacade.get_pending`/`get_all`）、12-inner-life（`InnerLifeFacade.get_state` + `activity_end` 的 `energy_delta` 契约）、13-activity-scheduler（四个纯函数）、02-config（`ActivityConfig` / `ExplorationConfig`）、03-llm（`LlmClient.complete`）、04-db（`activity` 表）、eval（`Evaluator`，OOC 轻量告警）

## 用户故事

> 作为 Nyx 系统的开发者，我想要活动系统的门面——`on_tick`/`on_desire_generated` 触发消费欲望、`select_activity` 选活动、后台 task 执行（读书/创作/发呆/自由探索/观察/休息）、`complete_activity` 判定 goal 并发布 `activity_end`、`interrupt` 抢占即暂停（可续活动）或废弃、同日程块内恢复 PAUSED 记录、`get_current`/`get_schedule` 供仪表盘——以便欲望「达峰→生成→被消费→满足回写」闭环，前端能看到活动时间线、打断点、进度。

## 验收标准

- [ ] `store.py` 含 `ActivityStore`（`insert` / `get` / `get_current` / `get_paused_in_block` / `get_last_exploration` / `list_schedule` / `list_results` / `update`）（实现见 `nyx/activity/store.py`）
- [ ] `facade.py` 含 `ActivityFacade`：`on_tick(tick_type) -> None` / `on_desire_generated(event) -> None` / `select_activity(desires, state) -> Activity | None` / `complete_activity(activity) -> None` / `interrupt(activity_id, by_event) -> None` / `get_current() -> Activity | None` / `get_schedule() -> list[Activity]` / `get_results() -> list[Activity]` / `list_materials() -> list[Material]` / `register_material(path, filename, total_chars) -> None`
- [ ] `select_activity` 纯决策：无欲望→`None`；精力不足→`REST`；否则第一个可排程欲望→映射活动，`progress` 存 `desire_id`/`goal`/`correlation_id`/`description`
- [ ] `READING` 升级 `FREE_EXPLORATION`：探索欲映射的读书在 `_maybe_start_activity` 里经 `should_explore`（频率上限）判定升级；频率上限内降级为普通读书
- [ ] 空槽默认：`select_activity` 返回 `None`（无欲望/全互动欲）时 `_maybe_start_activity` 产 `_default_activity`（精力疲惫 `< ENERGY_REST_THRESHOLD`→`IDLE_REFLECTION`、否则→`OBSERVE_USER`），`progress["desire_id"] is None`
- [ ] 活动执行在**后台 task**（不阻塞事件总线）；`activity_start`/`activity_end`/`activity_interrupted` 由 facade 自己 `publish`、`source=INTERNAL`
- [ ] `complete_activity`：goal 判定（`_goal_met` 纯函数）→ `status=COMPLETED` → 发布 `activity_end`（content 含 `activity_id`/`type`/`desire_id`/`goal_met`/`energy_delta`/`result`）
- [ ] `interrupt`：先校验目标 activity 存在且 RUNNING → cancel 执行 task 并 await 其结束 → 重读守卫 → 可续活动（`_RESUMABLE_TYPES`：READING/CREATION/FREE_EXPLORATION）置 `PAUSED`、其余置 `ABANDONED` + 发布 `activity_interrupted`（content `{activity_id, by}`）
- [ ] 同日程块内恢复：`_maybe_start_activity` 在查 running 后、欲望排序前查 `get_paused_in_block(当前块)`，命中则恢复同一记录（READING 从 `material_store.get_by_path` 刷新 `read_chars`/`total_chars` 续读；CREATION/FREE_EXPLORATION 无中间态重跑）；未命中再走欲望排序/空槽默认
- [ ] `material_store.py` 含 `MaterialStore`（`upsert` / `next_readable` / `find_by_topic` / `get_by_path` / `advance` / `append_fragment` / `get_fragments` / `list_all`），`get_by_path` 供读书恢复续读、`list_all` 供资料面板进度展示
- [ ] `exploration.py` 含 `Exploration`（线性 run）+ `should_explore` 纯函数；`web_enabled=false` 时 `_search` 直落本地搜索；LLM 调用带 `correlation_id` 溯源
- [ ] `observe.py` 含 `classify_presence` 纯函数（活跃度+窗口标题 → `"online"`/`"away"`/`"busy"`）与 `build_observation_summary` 纯函数（presence/窗口标题/屏幕摘要 → 观察 summary）；`screen.py` 含 `capture_screen` + `ScreenObserver`（周期抓屏 → 视觉描述 → 回调）
- [ ] 两处 LLM 产出后紧跟 `await evaluator.evaluate(output)`：`_run_llm_activity`（`output_type` "reading"/"creation"）与 `Exploration._summarize`（`output_type="exploration_finalize"`）
- [ ] `pyright` strict 零报错

## 技术方案

- **新文件**：`nyx/activity/store.py`、`nyx/activity/facade.py`、`nyx/activity/exploration.py`、`nyx/activity/observe.py`、`nyx/activity/screen.py`、`nyx/activity/material_store.py`（`scheduler.py` 归 13）
- **库**：无新增（探索线性化后不再依赖 `langgraph`）
- **ActivityStore 归属**：memory/desire/inner_life 各有 `store.py`，activity 保持一致——facade 不直接写 SQL（三层：Facade → 子系统 → 内部类）。tech-ref §7 ripple：`activity/` 补一行 `store.py  # ActivityStore（activity 表单表 CRUD）`
- **依赖解环（遵守 12 §54）**：`inner_life → {activity, desire}` 已锁，故 `ActivityFacade` **不持有 `InnerLifeFacade`**，注入 `get_state: Callable[[], Awaitable[CurrentState]]` 回调（组合根用 `inner_life.get_state` 绑定）。`select_activity(desires, state)` 以参数收 `CurrentState`（纯决策，无环）；`DesireFacade` 依赖单向（activity → desire，读队列/values），不成环
- **两个事件入口都归到 `_maybe_start_activity`**：`SCHEDULE_BLOCK_START` tick（每小时一块）与 `DESIRE_GENERATED`（欲望刚生成）都「有空闲就消费」。区别是触发时机，逻辑共用；有 running 活动则忽略（等它完成或下一个触发）
- **`select_activity` 返回 `Activity | None`**：无欲望 / 全互动欲时无活动可排，返回 `None`（空槽）。tech-ref §5 原签名 `-> Activity` 需 ripple 为 `-> Activity | None`（见完成定义）
- **活动执行 = 后台 task**：05-event「顺序分发、逐个 await handler」，若 on_tick 里 await 完整个活动（LLM 秒级、探索链分钟级）会阻塞事件总线、吞掉用户消息打断。故 `_maybe_start_activity` 用 `asyncio.create_task` 启动执行后立即返回；`interrupt` 靠 `self._task.cancel()` 软打断
- **并发守卫（同一时刻仅一个活动）**：`_start_lock` 串行化「查 running → insert PENDING → 翻 RUNNING」决策；但 `_execute` 在锁外异步翻 RUNNING，仅靠 `get_current`（只匹配 running，见 store）会留 TOCTOU 窗口（PENDING 已 insert 却查不到 running）。故锁内先同步查 `self._task` 未完成即 `return` 闭合窗口；`self._task` 在锁内赋值，天然串行
- **执行失败 = INCOMPLETE + 上抛**：`_execute` 失败落 `INCOMPLETE`（`ended_at` 已记）后仍 `raise`（不吞异常）；`logger.exception` 记录详情，`add_done_callback(_harvest_task_exception)` 收割 fire-and-forget task 的异常，避免 asyncio「Task exception was never retrieved」警告静默漂着
- **欲望状态接线（11 的 `mark_active`/`mark_suppressed`，V2）**：活动真正开始消费欲望时标 ACTIVE、非满足路径退出时释放 SUPPRESSED。三处均守卫 `isinstance(desire_id, str)`：`_execute` 置 RUNNING 后 `await self._desire.mark_active(desire_id)`（PENDING → ACTIVE）；`interrupt` 置 PAUSED/ABANDONED 落库后 `await self._desire.mark_suppressed(desire_id)`（ACTIVE → SUPPRESSED）；`_execute` 异常分支落 INCOMPLETE 后 `await self._desire.mark_suppressed(desire_id)`（ACTIVE → SUPPRESSED）。满足路径走既有 `complete_activity → ACTIVITY_END → satisfy`，由 11 的 `satisfy` 里「ACTIVE → PENDING」先行释放；续做路径（`_execute(resumed)`）`mark_active` 对 SUPPRESSED 是 no-op（守卫只 PENDING→ACTIVE），完成时 `satisfy` 从 SUPPRESSED 直达 SATISFIED 合法
- **自由探索升级（design §8.6，13 已委托给 14）**：`select_activity` 保持基线映射（探索欲→`READING`），升级判定放 `_maybe_start_activity`（那里有 store/config/now，`select_activity` 保持纯决策）。「探索欲」条件由结构保证——`READING` 活动**仅**由 `DesireType.EXPLORATION` 映射而来（13 `desire_to_activity`），故调用方在 `activity.type is READING` 时才调 `should_explore`（只查频率一项）
- **读书 = 读本地书库（禁凭空编造，design §8.2 落地）**：`MaterialStore`（`material` 表）存用户喂的读物与分块进度。`register_material`（`POST /api/upload` 入口）只 `upsert` 注册书库、**不立即读书**；读书统一由欲望驱动的 `READING` 在 `_maybe_start_activity` 里**先按 `goal.topic` 走 `find_by_topic`**（命中读那本，C2）、否则 `next_readable()` 取**最近未读完的那本**续读，读完自动换下一本。**无书可读**（`next_readable()` 返回 None）→ **仅当 `goal.topic` 非空**（由 11 的 seed 确定性钉死）才经 `should_explore` 转 `FREE_EXPLORATION`（限速中则退回默认活动）；无 topic（无 seed）或限速中一律退回默认活动——任何路径都不让 LLM 凭空编造主题或读书内容。三层兜底：`_maybe_start_activity` 不产无 source 的 READING、`_run_activity` 缺 source `raise`、`_run_reading_source` 只读真实文件块（空块聚合已有片段，不凭空编造）
- **六种活动执行分派（`_run_activity`）**：
  - `READING`：`_run_reading_source` 分块读真实文件（切 `[read_chars, read_chars+6000)` 一块喂 LLM 产 `{book, note}`）→ result 附 `read_chars`/`total_chars` 推进进度；缺 `source` 直接 `raise ValueError`（**禁凭空编造**）；读到最后一块/空块时聚合全部片段 → 完整笔记落盘（`_aggregate_note` 1 次 LLM）。**滚动摘要接力**：续读时把「上次已读到第 N 字 + 此前片段笔记（`get_fragments`）」拼进 `extra_context`（`书名 + 上次已读 + 本次新读（第 N~M 字）`），让本次 note 自然承接已读部分、只续写本块新内容，避免几篇之间不连贯
  - `CREATION`：1 次 LLM（`json_mode=True`、`module="activity"`、`output_type="creation"`）→ result `{title, content}`，再把标题 `_sanitize_filename` 清洗成安全文件名落盘 `workspace/creations/<safe>.md`（`file_io` write），result 附 `path`。**创作注入人格声音 + 此刻心境**：system prompt 用 `_build_creation_system(canon, state)` 拼「canon 全文（含 §说话风格）+ 此刻心境（emotion/valence/arousal/energy/active_desires）+ 正向创作指令 + JSON 约束」，补上创作路径此前缺失的 canon（canon 只进对话、不进 `_ACTIVITY_SYSTEM`）——读书仍走 `_ACTIVITY_SYSTEM` 不动
  - `IDLE_REFLECTION`：直接 `await self._reflect`（组合根注入的 reflect 回调，1 LLM 在 inner_life），不发 `REFLECTION` 事件；result 回带 `{summary}`
  - `FREE_EXPLORATION`：调 `Exploration.run()`（线性：搜 → 抓正文 → 一次总结，topic = 欲望描述）→ result `{type, outcome, summary, core_discovery, knowledge, new_topics, strong_new_topics, findings}`。终局 `_finalize_exploration_sink` 回写：`strong_new_topics`（LLM 已归并为抽象源话题）经 `desire.add_long_term` 落长期欲望（统一走 11 的去重/容量），`knowledge` 经 `memory.remember_knowledge` 落长期记忆
  - `OBSERVE_USER`：调组合根注入的 `get_observation`（0 LLM）产 `{presence, window_title, screen_summary}`，`summary` 由 `build_observation_summary` 拼装（窗口优先、屏幕次之）；`REST`：0 LLM，result `{}`
- **空槽默认（design §8.2 观察/发呆，13 §30 委托 14）**：`select_activity` 返回 `None`（无欲望/全互动欲）时 `_maybe_start_activity` 产 `_default_activity`——精力疲惫（`< ENERGY_REST_THRESHOLD`，从 12 `inner_life.emotion` 共享导入）→ `IDLE_REFLECTION`（+10 微恢复 + 反思回带 summary），否则 → `OBSERVE_USER`（-10 消耗 + 情报收集）。这是 `IDLE_REFLECTION`/`OBSERVE_USER` 的唯一触发来源（非欲望驱动、不进 13 `build_schedule`），补上后两条分支可达，不再死代码
- **`activity_end` content 契约（11 §49 + 12 §45 引用，本 spec 定义完整形状）**：`{"activity_id": str, "type": str, "desire_id": str | None, "goal_met": bool | None, "energy_delta": float, "result": dict}`。`desire_id`/`goal_met`/`type` 由 11 `satisfy_from_activity_end` 消费（`desire_id`/`goal_met` 缺键/错类型跳过；`type` ∈ {reading, free_exploration} 额外给创造欲加压 `_CREATION_ACTIVITY_PRESSURE_DELTA`，creation 结束不自循环）；`energy_delta` 由 12 `_apply_energy` 消费（缺省 0）；`type`/`result` 由 09 `remember_activity` 消费（活动记忆）；`result` 进 SSE payload（tech-ref §4）
- **`energy_delta` 取值**：`getattr(config.energy_delta, activity.type.value)`（`ActivityType.value` 与 `ActivityEnergyDelta` 字段名 1:1，`reading→-20`、`creation→-25`、`free_exploration→-30`、`observe_user→-10`、`idle_reflection→+10`、`rest→+30`），不用 if-elif（六键自然对应）
- **goal 判定（C3 精确版）**：`_goal_met(goal, result)` = goal None → `None`；否则按 `action` 判「本次是否完成一个单位」——`read` → `result.completed`（读完整本）、`write` → 有 `title`+`content`、`observe` → 有 `presence`；其余 → `False`。
- **精力门槛**：`select_activity` 用 13 的 `build_schedule(desires, state.energy, energy_delta)` 取 `[0]`（精力跌破阈值自动穿插 `REST`），不另写门槛逻辑；`schedule[0] is REST` → 无关联 desire
- **`get_schedule()` 语义**：返回「今日已产生的 Activity 记录」（`started_at >= 今日零点`，`list_schedule`），按 `started_at ASC`；`current`（running）也在 schedule 内。未来计划不持久化（design §8.1），前端按单条时间线渲染已产生记录（running 加「◀ 现在」标记），**不画未来空槽**；`_day_start` 纯函数算当日零点（MVP 用 UTC 日边界，可推翻）
- **`interrupt` 的 `by_event: EventType`**：打断原因（`USER_MESSAGE` / `INITIATE_CHAT`）。谁调 `interrupt` 归 17/18（用户消息/搭话打断活动）；14 只提供方法 + 发布 `activity_interrupted`。可续活动（`_RESUMABLE_TYPES`：READING/CREATION/FREE_EXPLORATION）打断置 `PAUSED`（保留记录 + 欲望关联），其余瞬时无进度的活动（发呆/观察/休息）仍置 `ABANDONED` 终态
- **恢复/续做（design §3.3 抢占语义落地）**：`interrupt` 对 `_RESUMABLE_TYPES` 置 `PAUSED` 而非废弃，`progress` 里的 `desire_id`/`goal`/`correlation_id` 保留。`_maybe_start_activity` 在「查 running → 查当前块 PAUSED → 恢复」——命中则复用同一 id 重跑：READING 从 `material_store.get_by_path(source)` 刷新 `read_chars`/`total_chars` 续读（书库进度是唯一持久进度）；CREATION/FREE_EXPLORATION 无中间态、整段重跑（探索不 checkpoint 中间 findings）。恢复不新建记录、不重新消耗欲望；跨日程块（`get_paused_in_block` 按 `schedule_block_id` 过滤）不恢复，旧 PAUSED 留档可查
- **`observe.py` 与观察状态的分工**：`classify_presence` 是「在线/离开/忙碌」三态判定的**单一事实来源**（纯函数、单测锁定）。采集（键盘/鼠标活跃度 + 前台窗口标题）在前端 Tauri 壳（design §2 进程边界），判定结果作为 `OBSERVATION_STATE` 事件推给 Python，ROUTING 到 inner_life + desire。**`classify_presence` 的运行时调用方是前端 ingress，不在本 spec 的 backend 范围内**（前端 spec 推迟）——保留它是为了让「判定规则」在 Python 侧可展示（原则 3）+ 可溯源（原则 5）。`OBSERVE_USER` 活动本身是 0-LLM（调注入的 `get_observation` 产 `{presence, window_title, screen_summary}`），`summary` 由 `build_observation_summary` 拼装
- **明确不做**：不建「计划」表（design §8.1 临时概念）；`classify_presence` 只覆盖键盘/鼠标/窗口三输入（屏幕视觉不扩展它）
- **屏幕视觉（design §8.5 落地，opt-in）**：`vision.enabled`（config）开启时，组合根注入的 `get_observation` 返回 `screen_summary`——`ScreenObserver` 周期抓屏（Pillow ImageGrab，`asyncio.to_thread`）→ `VisionClient` 视觉描述 → `app.last_screen_summary` 折入。`OBSERVE_USER` 的 `summary` 由 `build_observation_summary` 拼装（窗口标题优先、屏幕摘要次之）。视觉**丰富观察摘要**、不扩展 `classify_presence`（在线判定仍只靠键盘/鼠标/窗口三输入）；`ScreenObserver`/`VisionClient` 失败 best-effort 返 `None`，主流程正确性不依赖其产出。

## 测试要点

- [ ] 单元测试 `tests/test_activity/`（`pytest-asyncio`；`db = await connect(":memory:")`；fake `LlmClient.complete` 按 `output_type` 返回 fixture JSON；`EventBus` 真实例 + recording handler，`run()` 作 task；`get_state` 用 fake 回调返回预设 `CurrentState`——同 05/09/11/12 模式）：
  - [ ] **store**（`test_activity_store.py`）：`insert + get` 往返（`progress` JSON 往返、枚举 `.value` 往返）；`get_current` 只取 running 最新一条；`get_paused_in_block`（当前块最新 PAUSED、忽略其他块；无则 None）；`get_last_exploration`（无 free_exploration 记录 → `0.0`，有 → `MAX(started_at)`）；`list_schedule(start)` 按 `started_at >= start` 过滤 + ASC；`list_results` 只回 completed + 读书/探索/创作三类按 `ended_at DESC`；`update` 改 `status`/`progress`/`ended_at` → `get` 验证
  - [ ] **material_store**（`test_material_store.py`）：`get_by_path`（upsert+advance 后取到最新 `read_chars`；缺路径 → None）
  - [ ] **纯函数**（`test_activity_facade.py`）：`_day_start`（`now=86400*1.5 → 86400.0`）；`_schedule_block_id`（同网格块内多个 now 返回同标签、跨块返回不同标签、跨小时边界正确进位）；`_goal_met`（goal None → None；goal 非 None + result 空 → False；goal 非 None + result 非空 → True）；`_pick_creation_style`（返回 6 风格之一）；`_build_creation_context`（有风格/主题/知识/屏幕各段；无知识无屏幕 → 省略对应段）
  - [ ] **select_activity**（fake `get_state` 返回 `energy=80`）：无欲望 → `None`；`[探索欲]` → `type is READING`、`progress["desire_id"] == desire.id`、`goal` 序列化正确、`progress["description"] == desire.description`；`[互动欲]` → `None`（不占日程块）；`[休息欲]` → `type is REST`、`progress["desire_id"] == rest_desire.id`（欲望驱动的 REST 保留关联）；`energy=30` + 探索欲 → `type is REST`、`progress["desire_id"] is None`（精力恢复无关联）
  - [ ] **should_explore**（`test_exploration.py`）：`last=1000` + `now-last < 1h*3600` → False；`last=0.0` + 频率过 → True
  - [ ] **facade 生命周期**：
    - [ ] `_maybe_start_activity`：有 running 活动 → 不新起；无欲望 → 产 `_default_activity`（见空槽默认 bullet）并 insert；有欲望 → insert + 发布 `activity_start` + `activity_end`（`content["type"]`/`desire_id`/`goal_met`/`energy_delta`/`result` 正确、`source is INTERNAL`）；READING/CREATION 时 `evaluator.evaluate` 被调 1 次（收到该 `LLMOutput`）
    - [ ] 升级路径：探索欲（`goal.topic` 非空）+ 频率过 → `activity.type is FREE_EXPLORATION`；频率未过 → 降级 `READING`；无 topic（无 seed）→ 退回默认活动（不转自由探索）
    - [ ] 空槽默认：无欲望 + `energy=30` → `type is IDLE_REFLECTION`、`progress["desire_id"] is None`；无欲望 + `energy=80` → `type is OBSERVE_USER`、`progress["desire_id"] is None`
    - [ ] `complete_activity`：`status is COMPLETED`、`ended_at` 非 None、发布 `activity_end`（`energy_delta == config.energy_delta.reading` 等）
    - [ ] `interrupt`：RUNNING 活动 → cancel + 可续活动 `status is PAUSED`、非可续 `status is ABANDONED` + 发布 `activity_interrupted`（`content["by"]` 正确）；`activity_id` 不存在 → 不 cancel、不发布；执行中活动挂起在可取消 await 上时 interrupt → 终态 `PAUSED`/`ABANDONED` 而非被 complete 覆盖
    - [ ] 恢复：`_maybe_start_activity` 命中当前块 PAUSED 创作 → 复用同一 id 重跑（id 不变、COMPLETED、evaluator 再调 1 次）；命中 PAUSED 读书 → 从 material 层刷新 `read_chars` 续读；不同块旧 PAUSED → 不恢复、走新建默认活动
    - [ ] `get_current` / `get_schedule` / `get_results` 委托 store
    - [ ] **读书知识点提取（R1）**：mock LLM 返回 `{"points":[{"topic","content"}...]}` → `_memory.remember_knowledge` 收到同批 items（tag 由 memory 层写 "knowledge"）；mock LLM 抛异常 → 不冒泡、读书活动仍 COMPLETED（best-effort）；`points` 非 list / 超 5 条截断到 5
    - [ ] **分块提取**：长正文（> `_READ_CONTEXT_CHARS`）切成多块逐个喂 LLM，每块 `正文` ≤ 6000 字、块数 ≤ `_KNOWLEDGE_MAX_CHUNKS`；跨块重复知识点按 content 去重，总量 ≤ `_KNOWLEDGE_MAX_POINTS`
    - [ ] **`chunk==""` 完成分支**：读到末尾（文件比注册时短）时既聚合笔记也调 `_extract_knowledge`（此前漏调）
    - [ ] **创作上下文（W1/W2/W3）**：创作活动执行时 `list_memories(tag="knowledge")` 被调、`_get_observation` 被调、`_run_llm_activity` 收到 `context_label="创作参考"` 且 `extra_context` 含风格/知识/屏幕（`_FakeMemory`/`_FakeObservation` 桩）
  - [ ] **exploration**（`test_exploration.py`）：`Exploration` 用 fake llm/fake_evaluator/tools，`web_enabled=false` 时 `_search` 只调 `local_search`、`run(topic, correlation_id)` 返回 `{type, outcome, summary, core_discovery, knowledge, new_topics, strong_new_topics, findings}`；`_summarize` 的 `llm.complete` 收到 `correlation_id == 初始 correlation_id`、`output_type="exploration_finalize"`，且 `complete` 后 `evaluator.evaluate` 被调；总结 JSON 非对象 → 兜底空结果（`core_discovery==""` → `outcome=="exhausted"`）；`web_enabled=true` 时 `web_search` 空则兜底 `local_search`、抓正文失败（`web_fetch` 抛）不崩 run（snippet 兜底）；`_result_parts` 解析 dict/str/非法
  - [ ] **observe**（`test_observe.py`）：`classify_presence` 三态判定（活跃→online、窗口标题→busy、无→away）；`build_observation_summary` 四态拼接（有窗口无屏幕 / 无窗口无屏幕 / 窗口+屏幕 / 无窗口有屏幕）
  - [ ] **screen**（`test_screen.py`）：`ScreenObserver.sample_once` 抓屏+describe 各 1 次返描述文本；capture 抛异常 → 返 `None` 不崩；describe 抛异常 → 返 `None` 不崩（best-effort）
- [ ] 集成测试：无（LLM 全 mock、DB 用 `:memory:`；与 desire/inner_life 的真实编排归 18-api）
- [ ] E2E 测试：无

## 完成定义

- [ ] `ruff check` 零报错
- [ ] `pyright` 零报错
- [ ] `pytest` 全绿
- [ ] `test-inventory.md` 已更新
- [ ] ripple 同步：tech-ref §7 `activity/` 补 `store.py`；§5 `ActivityFacade` 构造参数补 `memory`；§5 `select_activity` 返回类型 `Activity` → `Activity | None` 且 `async def` → `def`（纯决策，与 `_default_activity` 一致）；`activity_end` content 契约（`desire_id`/`goal_met`/`energy_delta`）与 11 §49 + 12 §45 一致
- [ ] ripple 同步：`interrupt` 语义「可续置 PAUSED、其余 ABANDONED」与 design §3.3 抢占语义一致；`ActivityStore.get_paused_in_block` / `MaterialStore.get_by_path` 补进 tech-ref §7；`_maybe_start_activity` 恢复路径与 17-expression 打断入口约定（搭话/回复打断活动调 `interrupt`）一致
- [ ] 下游约定：17-expression 搭话/回复打断活动时调 `interrupt(activity_id, by_event)`；18-api 组合根注入 `get_state=inner_life.get_state`、`evaluator`（给 `ActivityFacade` 与 `Exploration` 的 LLM 产出评分）、订阅 `SCHEDULE_BLOCK_START`（on_tick）与 `DESIRE_GENERATED`（on_desire_generated）
- [ ] ripple 同步（屏幕视觉）：tech-ref §7 `activity/` 补 `screen.py`、§8 补 `vision:` 段；`OBSERVE_USER` 观察 result 契约由 `{presence, window_title, summary}` 扩展为 `{presence, window_title, screen_summary, summary}`（`result.summary` 仍由 `build_observation_summary` 拼装，09 `remember_activity` 消费不变）
