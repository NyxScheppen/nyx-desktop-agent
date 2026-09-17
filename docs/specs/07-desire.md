# 欲望系统（desire）：值机制、store、全周期与门面

> 范围：`desire/value.py`（值机制纯函数与常量）、`desire/store.py`（`DesireStore` 三类领域表 + 恢复 marker CRUD）、`desire/lifecycle.py`（`DesireLifecycle` 全周期编排）、`desire/facade.py`（`DesireFacade` 门面）。
> 值机制负责压力值、表达权重、抑制阈值的数学语义；生命周期负责加压/衰减/达峰生成/满足/淘汰；纯 CRUD 在 `store.py`；`facade.py` 是薄门面（事件入口 + 读委托）。
> spec 只定义契约（签名 + 数学语义 + 全周期编排语义 + 阈值/增量决策）；实现以 `nyx/desire/value.py` / `nyx/desire/store.py` / `nyx/desire/lifecycle.py` / `nyx/desire/facade.py` 源文件为准。

## 元信息

- **前置依赖**：01-types（`DesireType` / `DesireStatus` / `DesireValue` / `ShortTermDesire` / `LongTermDesire` / `Goal` / `DesireState` / `GoalAction` / `Event` / `EventType` / `Source`）、02-config（`DesireConfig`：`peak_threshold` / `retry_limit` / `long_term_capacity` / `short_term_capacity` / `value_decay`）、03-llm（`LlmClient.complete`）、04-module-bus-system（`Database`、`EventBus`、欲望表、`desire_generation_attempt` / `desire_eval_applied`）、10-eval（`Evaluator`）
- **本 spec 带来的连锁改动（ripple，已同步）**：01-types 给 `LongTermDesire` 加 `type` 字段、`DesireValue` 加 `updated_at` 字段；04-module-bus-system 给 `long_term_desire` 加 `type` 列、`desire_value` 加 `updated_at` 列；tech-ref 补 `desire/value.py` 与 `desire/store.py`；本轮为 `DesireConfig` 增加 `short_term_capacity`，并为 `long_term_desire.name_normalized` 建唯一索引。

## 用户故事

> 作为 Nyx 系统的开发者，我想要 `DesireFacade` 把欲望全周期（观察加压、达峰生成、满足/淘汰回写）统一成一个门面，以便 `activity` 只调 `get_pending` 消费、`inner_life`/`activity` 只靠事件回写满足、仪表盘只调 `get_all` 快照；值机制纯函数收口在 `value.py`，领域与恢复状态 CRUD 收口在 `store.py`，全周期编排在 `lifecycle.py`，所有 LLM 调用和事件发布走可注入的 `llm` / `bus`。

## 验收标准

- [ ] `store.py` 含 `DesireStore`（`add_desire` / `get_desire` / `list_pending` / `list_suppressed` / `list_short_term` / `update_desire` / `get_value` / `list_values` / `upsert_value` / `apply_value_delta` / `reset_value_if_unchanged` / `try_mark_eval_applied` / `claim_for_activity` / `trim_pending` / `insert_long_term_if_available` / `insert_long_term` / `list_long_term` / `update_long_term` / 生成尝试 CRUD）+ 序列化 helper（实现见 `nyx/desire/store.py`）
- [ ] `lifecycle.py` 含 `DesireLifecycle`（`pressure_from_observation` / `pressure_creation` / `satisfy_from_activity_end` / `run_eval` / `satisfy` / `expire` / `mark_active` / `mark_suppressed`）+ `_parse_desire` / `_subtopics_for` / `_subtopic_freshness` / `_pick_topic_seed` / `_most_relevant_long_term` / `_build_desire_prompt`（实现见 `nyx/desire/lifecycle.py`）
- [ ] `facade.py` 含 `DesireFacade`，公开方法包括：`add_value(source: Event, consumer_id: str | None = None) -> None` / `evaluate(energy: float = 100.0, event_id: str | None = None) -> list[ShortTermDesire]` / `pressure_creation(delta: float) -> None` / `get_pending() -> list[ShortTermDesire]` / `get_all() -> DesireState` / `satisfy(desire_id: str, goal_met: bool) -> None` / `expire(desire_id: str) -> None` / `mark_active(desire_id: str) -> None` / `mark_suppressed(desire_id: str) -> None` / `release_active(desire_id: str) -> None` / `claim_for_activity(desire_id: str) -> bool` / `claim_for_activity_in_transaction(desire_id: str) -> bool` / `add_long_term(desire: LongTermDesire) -> None`
- [ ] `add_value` 是**事件入口**（对 tech-ref「加压」注释的精确化）：`OBSERVATION_STATE` → 互动欲加压，`ACTIVITY_END` → 解析满足信号回写；其余类型忽略
- [ ] `run_eval`：先四类型衰减（`elapsed_days` 来自 `updated_at`）→ 长期欲望周期加压 → 疲惫加压（`energy < ENERGY_REST_THRESHOLD` → 休息欲 +`_REST_PRESSURE_DELTA`）→ 达峰判定（`at_peak and is_expressible`）→ **只生成最迫切的 1 个**（value 最高）→ LLM 生成 → 重置该类型 value → 入队 → 发布 `desire_generated`；无达峰返回 `[]`，非选中类型**保留压力**（不重置）
- [ ] `satisfy(goal_met=True, goal=None)`：出队（`SATISFIED`）+ 表达权重正强化 + 长期进度回写 + 发布 `desire_satisfied`
- [ ] `satisfy(goal_met=True, goal 非 None)`：`goal_progress+1` 累计；`>= goal.count` 才满足（出队 + 强化 + 回写 + 发布），否则保持 `PENDING`（累计进度，不重复满足）
- [ ] `satisfy(goal_met=False)`：`retry_count+1`；`> retry_limit` → 放弃（`EXPIRED` + 值回增 + 抑制阈值上浮 + 发布 `desire_expired`）；否则保持 `PENDING`（`created_at` 不变，`list_pending` 的 `created_at ASC` FIFO 天然靠前，无显式插队动作）
- [ ] `claim_for_activity`：使用条件更新原子完成 `PENDING → ACTIVE`，仅一次调用成功；活动 starter 在同一数据库事务中完成领取和活动插入，任一步失败整体回滚。
- [ ] `mark_active`：保留兼容入口；真实活动已通过 claim 领取时为幂等 no-op
- [ ] `mark_suppressed`：`ACTIVE → SUPPRESSED`（活动中断/异常停车，不立即重试），仅 ACTIVE 可转、其余幂等 no-op
- [ ] `run_eval` 释放：`SUPPRESSED` 欲望其类型仍可表达（`is_expressible`）→ `PENDING` 放回队列；不可表达保持 `SUPPRESSED`
- [ ] `expire`：`EXPIRED` + 值回增 + 抑制阈值上浮 + 发布 `desire_expired`
- [ ] `add_long_term(desire)`：名称先规范化（`strip`、`casefold`、连续空白折叠）；容量检查、规范化名称唯一性检查和插入在同一事务内再次确认。embedding 是严格前置：启用 embedding 时，本欲望和候选欲望任一 embedding 失败都直接失败且不插入，不降级为仅名称去重；余弦达到 `_LT_DEDUP_SIM_THRESHOLD` 时跳过。
- [ ] 事件发布遵守「Facade 自己 publish、绝不返回 Event」；事件 `source=INTERNAL`；`desire_satisfied` / `desire_expired` 的 `correlation_id` = `desire.id`
- [ ] `run_eval` 的 LLM 产出（`output_type="desire"`）后紧跟 `await evaluator.evaluate(output)`（漏记由测试断言兜底）；解析成功的产出先保存到 `desire_generation_attempt`，正式提交失败后的重试必须复用该 JSON，不重复调用 LLM。
- [ ] `run_eval` 的本地状态使用评估锁和 `updated_at` 条件重置；评估期间发生的新压力不得被旧快照覆盖。
- [ ] durable tick 传入 `event_id` 时，周期衰减/压力/SUPPRESSED 释放与 `desire_eval_applied` marker 同事务提交；同一 tick 重试继续生成阶段但不重复结算周期状态。兼容直调 `event_id=None` 时每次照常结算。
- [ ] `run_eval` 生成后按 `DesireConfig.short_term_capacity` 裁剪待消费短期欲望；保留排序为 `expression_weight DESC`、`created_at ASC`，低权重记录被移除。若新记录被裁剪，不发布 `desire_generated`。
- [ ] `pyright` strict 零报错

## 值机制

### 公开函数与常量

`nyx.desire.value` 提供以下纯函数和公开步长常量。函数不访问数据库、配置、事件总线或时钟；生命周期由本 spec 的 `DesireLifecycle` 注入配置并负责编排。

- `decay_value(value: float, elapsed_days: float, rate: float) -> float`
- `apply_pressure(value: float, delta: float) -> float`
- `reinforce_weight(weight: float, delta: float = WEIGHT_REINFORCE_DELTA) -> float`
- `raise_suppression(threshold: float, delta: float = SUPPRESSION_RAISE_DELTA) -> float`
- `at_peak(value: float, peak_threshold: float) -> bool`
- `is_expressible(value: float, suppression_threshold: float) -> bool`
- `default_value(type_: DesireType) -> DesireValue`
- `WEIGHT_REINFORCE_DELTA = 0.05`
- `SUPPRESSION_RAISE_DELTA = 0.1`
- `REFUND_DELTA = 0.3`

公开导入面为：
`from nyx.desire.value import (decay_value, apply_pressure, reinforce_weight, raise_suppression, at_peak, is_expressible, default_value, WEIGHT_REINFORCE_DELTA, SUPPRESSION_RAISE_DELTA, REFUND_DELTA)`。
`_clamp`、范围常量和初始值常量为私有实现细节。

### 数学语义与边界

- `value`、`expression_weight`、`suppression_threshold` 均限制在 `[0, 1]`。
- `decay_value` 使用线性衰减：`max(0, value - rate * elapsed_days)`；`rate=0` 或 `elapsed_days=0` 时不变，下限为 `0`。
- `apply_pressure` 使用 `value + delta` 并夹到 `[0, 1]`，同时用于事件加压和放弃/淘汰后的回增；负 `delta` 也必须夹到下限。
- `reinforce_weight` 表达成功后增加表达权重，默认增加 `0.05`，上限为 `1.0`。
- `raise_suppression` 失败或被抑制后增加抑制阈值，默认增加 `0.1`，上限为 `1.0`。
- `at_peak(value, peak_threshold)` 的结果为 `value >= peak_threshold`；`is_expressible(value, suppression_threshold)` 的结果为 `value >= suppression_threshold`，两者都包含等号。
- 达峰并可表达的组合条件为 `at_peak(...) and is_expressible(...)`，等价于 `value >= max(peak_threshold, suppression_threshold)`，但保留两个函数以区分固定达峰阈值和动态抑制阈值。
- `default_value(type_)` 返回该类型的 `DesireValue`：`value=0.0`、`expression_weight=0.7`、`suppression_threshold=0.5`、`updated_at=0.0`。`updated_at=0.0` 是初始化哨兵，组合根写入数据库时覆盖为当前时间。

### 数值字段与生命周期的关系

- `value` 是压力值，由观察状态、长期欲望周期、疲惫和指定活动事件加压；按 `DesireConfig.value_decay` 衰减；达峰后触发短期欲望生成并在成功提交时重置为 `0`。
- `expression_weight` 是表达权重，满足一次后正强化；活动系统选择消费对象时使用该字段排序。
- `suppression_threshold` 是习得性抑制阈值，失败或抑制后上浮。初始值 `0.5` 低于默认达峰阈值，因此初始状态下达峰即可表达；多次失败后阈值可能高于达峰阈值，达峰也可能暂不表达。
- `decay_value` 的 `elapsed_days` 由生命周期根据 `desire_value.updated_at` 计算：`(now - updated_at) / 86400`。每次评估先衰减并结算，再将 `updated_at` 更新为当前时间；两次评估之间不实时下降。
- 加压增量的来源和具体值由生命周期定义，不由纯函数层猜测：观察 `+0.15`、长期欲望周期 `+0.1`、疲惫 `+0.1`、读书/自由探索结束时创造欲 `+0.15`。
- 回增统一调用 `apply_pressure(value, REFUND_DELTA)`，不额外引入回增函数。

## 技术方案

- **实现文件**：`nyx/desire/value.py`、`nyx/desire/store.py`、`nyx/desire/lifecycle.py`、`nyx/desire/facade.py`（无 API；`desire_eval_applied` 表结构由 04-module-bus-system 定义）
- **库**：无新库（标准库 `json` / `time` / `uuid` / `typing`；`aiosqlite` 已由 04-module-bus-system 引入）
- **公开面**：`from nyx.desire.value import ...`（本 spec 的值机制函数与常量）；`from nyx.desire.store import DesireStore`；`from nyx.desire.lifecycle import DesireLifecycle`；`from nyx.desire.facade import DesireFacade`（不加 `__all__`；序列化 helper 私有）
- **四层职责**：`DesireFacade`（Facade）→ `DesireLifecycle`（全周期编排）→ `DesireStore`（领域表与恢复 marker CRUD）；`value.py` 是被 lifecycle/store 直接调用的纯函数模块，不新增运行时抽象层。`lifecycle` 由 `facade` 内部构造（共享 store），让 `facade` 只做事件入口 + 读委托
- **store 锁约定（同 07）**：每个方法一个 `async with self._db.lock` 的 SQL 块；store 方法之间不互相调用对方的持锁方法（`asyncio.Lock` 不可重入）
- **两个读路径（`get_pending` vs `get_all`）**：tech-ref §5 把它们分开——`get_pending` = 待消费队列（`list_pending`，只含 `PENDING`、`created_at ASC` FIFO），供活动排期/拼 prompt；`get_all` = 全量快照（`short_term` 用 `list_short_term`，含 satisfied/expired 历史、`created_at DESC`），供 `/api/desires` 仪表盘。故 store 要两个 list 方法，`DesireState.short_term` 是「全部」而非「待消费」
- **可空 JSON 列（同 07 的 `embedding`）**：`short_term_desire.goal` 是 `Goal | None` ⟺ `goal TEXT` 可空，`None ↔ SQL NULL`（非 `"null"` 字符串）
- **`add_value` 是事件入口（决策，对 tech-reference 注释的精确化）**：tech-ref 写「活动/对话/长期欲望 加压」，但 ROUTING 里 desire 订阅了 `OBSERVATION_STATE` 和 `ACTIVITY_END` 两个事件——`OBSERVATION_STATE` 是加压、`ACTIVITY_END` 是满足回写（`04-module-bus-system` 的路由与事件语义、ROUTING 注释「满足」）。故 `add_value` 按 `source.type` 派发；组合根用 `bus.subscribe(EventType.OBSERVATION_STATE, facade.add_value)` + `bus.subscribe(EventType.ACTIVITY_END, facade.add_value)` 绑定
- **`evaluate()` 由 tick 触发**：TICK_ROUTING 的 `DESIRE_EVAL → desire`。runtime 不把完整 Event 传入 Facade，只把 `event.id` 作为 `event_id` 传给 `evaluate()`，用于周期状态幂等；兼容直调可省略。`desire_generated` 的 `correlation_id = desire.id`，不改成 tick correlation。
- **加压增量（默认值，标注可推翻）**：`_OBSERVATION_PRESSURE_DELTA=0.15`（观察状态→互动欲 +0.15）、`_LONG_TERM_PRESSURE_DELTA=0.1`（每个长期欲望周期→对应类型 +0.1）、`_REST_PRESSURE_DELTA=0.1`（疲惫 `energy < ENERGY_REST_THRESHOLD`→休息欲 +0.1）、`_CREATION_ACTIVITY_PRESSURE_DELTA=0.15`（读书/自由探索结束→创造欲 +0.15）。加压复用本 spec 值机制的 `apply_pressure`
- **衰减时机（决策：加 `updated_at` 列，已与用户确认）**：`elapsed_days = (now - updated_at) / 86400`，`decay_value(value, elapsed_days, config.value_decay)`。`updated_at` 记录"最后一次 value 变化"，每次 evaluate 先衰减结算再写回 `updated_at = now`；衰减是单调的，两次 evaluate 之间 value 不实时下降（与 06-memory-system 的 `decay_freshness` 一样属于按访问结算的当前实现限制），相对顺序不破坏
- **达峰生成（决策：只生成最迫切 1 个，已与用户确认）**：达峰判据 = `at_peak(value, peak) and is_expressible(value, suppression)`（本 spec 值机制的门控组合）；多个达峰类型时 `max(..., key=value)` 取最高者生成 1 个，**只重置选中类型**，其余达峰类型保留压力下次 evaluate 再生成——每次 evaluate 最多 1 次 LLM 调用（原则 1）
- **去重（decision，可推翻）**：`run_eval` 生成后、入队前两步判定——① **话题锚点优先**：新欲望 `goal.topic` 非 None 时，与 `list_pending()` 各待消费欲望的 `goal.topic` 精确相等即判重复丢弃（确定性、零误判、不依赖 embedding）；② **余弦兜底**：`goal.topic` 缺失（None）或未命中时，用注入的 `EmbedFn`（`memory/retrieval` 的 `build_embed`，与 memory/evaluator 共享同一实例）算新欲望 `description` 的 embedding，与 `list_pending()` 各 description embedding 做 `cosine` 比对，任一 `>= _DEDUP_SIM_THRESHOLD(0.9)` 判语义重复丢弃（不入队、不发布，value 已在重置步骤归零）。`embed=None`（向量层禁用）或 embed 抛异常降级为不去重（best-effort 旁路，同矛盾检测）
- **主题种子（decision，可推翻）**：`_pick_topic_seed` 按「没做过 / 新鲜度最低」从对应类型长期欲望的子主题池取——先查记忆（注入的 `list_memories` 回调，组合根接 `memory.list_memories`）做 substring 匹配，无命中记忆（= 没做过）最优先，都做过取新鲜度最低者；空池返回 `None`。种子拼进 `_build_desire_prompt` 给 LLM 作生成上下文；**探索欲的 `goal.topic` 由 seed 确定性钉死**——解析后 `goal is not None` 时强制 `goal.topic = seed`（无 seed 则清空为 `None`），杜绝 LLM 漂移主题（如名字撞车）；`goal=None` 时不合成 goal（保持单次满足语义），自由探索由 09-activity 的 topic 非空条件与 `should_explore` 限速规则兜底；**互动欲的 seed 同样承载进 `goal.topic`**——`goal` 常为 None，seed 存在时构造 `Goal(action=OBSERVE, count=1, topic=seed)`（count=1 保持「搭话一次即满足」语义不变），使互动欲也能按话题锚点去重
- **`strength` 语义**：`ShortTermDesire.strength` = 达峰时的 `value`（生成前保存，值重置后仍保留），供展示/排序
- **长期进度回写（decision，可推翻）**：满足时回写**最相关**的长期欲望 `progress += 0.1`（夹 `[0,1]`）、`strength -= 0.02`（夹 `[0,1]`）。`_most_relevant_long_term` 按 `goal.topic` 双向 substring 命中 `subtopics` 者优先，无 topic 或都不命中退回第一个 `type` 匹配；无 `type` 匹配返回 `None`（不回写）。**MVP 局限**：长期 `strength` 递减结果未被消费（prompt 读的是 `ShortTermDesire.strength`），接线 deferred（见 V3-roadmap）
- **长期欲望初始化（seed）**：3 个初始集来自 canon §4（硬编码），归组合根启动时 `insert_long_term`（表空才 seed）；四类型 `desire_value` 同样由组合根用 `default_value(t)` 初始化并覆盖 `updated_at=now`。07 只提供 store 原语，不提供 seed 方法；`long_term_capacity` 由 `add_long_term` 消费——长期欲望运行时新增有两个入口（08-inner-life 反思 + 09-activity 探索终局），统一走 `add_long_term` 归口去重 + 容量检查（满不新增，不淘汰）
- **五态流转（V2，`ACTIVE`/`SUPPRESSED` 纳入）**：`PENDING → ACTIVE` 由 `claim_for_activity` 原子领取；`ACTIVE → SATISFIED | EXPIRED`（满足时从 ACTIVE 释放并结算）；`ACTIVE → SUPPRESSED`；`SUPPRESSED → PENDING`（`run_eval` 里类型仍可表达即释放回队列）。`SUPPRESSED` 可逆、非终态；续做路径恢复同一记录时由活动完成结算，不重复领取。
- **`activity_end` 的满足信号契约（09-activity 引用）**：`event.content` 含 `desire_id`（`str | None`）与 `goal_met`（`bool | None`）。`satisfy_from_activity_end` 缺任一键或非预期类型即跳过（不抛），因为观察用户/发呆等活动无欲望可满足。额外：`event.content["type"]` 为 `reading` / `free_exploration`（`ActivityType.value`）时，满足逻辑之外再给创造欲加压 `_CREATION_ACTIVITY_PRESSURE_DELTA`（创作活动 `creation` 结束不自循环；`type` 缺失/其他值跳过）
- **新增 `output_type="desire"`**：`LLMOutput.type` 自由字符串，开放集合新增无冲突

## 测试要点

- [ ] 单元测试 `tests/test_desire/`（`pytest-asyncio`；`db = await connect(":memory:")`；`store = DesireStore(db)`；`lifecycle = DesireLifecycle(store, bus, fake_llm, fake_evaluator, config, fake_list_memories)`；fake `LlmClient.complete` 按 `output_type == "desire"` 返回 fixture JSON 并记录调用、fake `Evaluator.evaluate` 记录调用；`EventBus` 用真实例 + 订阅 recording handler，`run()` 作 task 驱动——同 05/09 模式）：
  - [ ] **值机制纯函数**（`tests/test_desire/test_value.py`，无 DB、无 mock）：
    - [ ] `decay_value`：`elapsed_days=0` 不变；按 `rate` 线性衰减；衰减到负数时夹到 `0`；`rate=0` 不变
    - [ ] `apply_pressure`：正增量上升；超过上限夹到 `1.0`；负增量夹到 `0.0`
    - [ ] `reinforce_weight`：默认增量为 `WEIGHT_REINFORCE_DELTA`；显式增量覆盖；超过上限夹到 `1.0`
    - [ ] `raise_suppression`：默认增量为 `SUPPRESSION_RAISE_DELTA`；显式增量覆盖；超过上限夹到 `1.0`
    - [ ] `at_peak` / `is_expressible`：低于阈值为 `False`，等于阈值为 `True`
    - [ ] 门控回归：初始抑制阈值低于达峰阈值时可表达；失败四次使抑制阈值高于达峰值时不可表达
    - [ ] `default_value`：四种 `DesireType` 均返回正确类型、`value=0.0`、`expression_weight=0.7`、`suppression_threshold=0.5`、`updated_at=0.0`
    - [ ] 常量边界：`0.0 <= WEIGHT_REINFORCE_DELTA <= SUPPRESSION_RAISE_DELTA`，`REFUND_DELTA > 0`
  - [ ] **store**（`test_desire_store.py`）：
    - [ ] `add_desire + get_desire` 往返：含 `goal=Goal(READ, 3, "骑士团")`、非默认 `retry_count`/`status` → 各字段全等（`goal` JSON 往返、枚举往返）
    - [ ] `goal=None` 往返 → `get_desire().goal is None`（SQL NULL 非 `"null"` 字符串）
    - [ ] `list_pending`：造 pending/active/satisfied/expired 各一条 → 只返回 pending，按 `created_at ASC` 排序
    - [ ] `list_suppressed`：造 suppressed 两条（`created_at` 乱序）+ pending/active 各一条 → 只返回 suppressed，按 `created_at ASC` 排序
    - [ ] `list_short_term`：同上四条 → 返回全部（含 satisfied/expired），按 `created_at DESC` 排序（区别于 `list_pending` 的过滤 + ASC）
    - [ ] `update_desire`：改 `status`/`retry_count` → `get_desire` 验证
    - [ ] `goal_progress` 往返：`add_desire` 带 `goal_progress=2` → `get` 往返；`update_desire` 改 `goal_progress=3` → 再 `get` 验证（goal 精确计数存储层）
    - [ ] `list_values + upsert_value`：`upsert_value` 新建 → `list_values` 返回；同 `type` 再 `upsert_value` 改 `value`/`updated_at`（ON CONFLICT 更新不重复建行）
    - [ ] `insert_long_term + list_long_term + update_long_term`：`subtopics`/`linked_values` JSON 数组往返、`type` 枚举往返；`update_long_term` 改 `progress`/`strength`
  - [ ] **lifecycle 纯函数**：
    - [ ] `_parse_desire`：合法 JSON（含 goal）→ `(description, Goal)`；`goal: null` → `(description, None)`；缺 `description` / 空串 → `ValueError`；`goal.action` 非法 → `ValueError`；`count` 非正/非 int → `ValueError`；`topic` 非 str → `ValueError`；JSON 是数组 → `ValueError`
    - [ ] `_subtopics_for`：有 `type` 匹配且 `subtopics` 非空的长期欲望 → 返回该 `subtopics`（过滤 `""`/空白子主题）；无匹配/空池 → `[]`
    - [ ] `_subtopic_freshness`：空串/纯空白 → `None`（通配符不做匹配）；非空命中 → 最新 freshness
    - [ ] `_pick_topic_seed`：空池 → `None`；全没做过（无命中记忆）→ 第一个；部分做过 → 取没做过的；都做过 → 取新鲜度最低者
    - [ ] `_most_relevant_long_term`：无 `type` 匹配 → `None`；`topic` 双向 substring 命中第二条 → 返回第二条；`topic` 轻微漂移仍命中；`topic=None` → 第一个；同类型都不命中 → 第一个
    - [ ] `_build_desire_prompt`：含类型 `.value` 与种子；`seed=None` → 含「（无）」
  - [ ] **pressure_from_observation**：互动欲 `value` 由 `x` → `min(1.0, x + 0.15)`；`updated_at` 更新
  - [ ] **pressure_creation**：创造欲 `value` 由 `x` → `min(1.0, x + delta)`（传 `delta=0.2`）；`updated_at` 更新
  - [ ] **satisfy_from_activity_end 活动结束加压**：`content["type"]="reading"` → 满足逻辑外创造欲 +0.15；`content["type"]="free_exploration"` → 创造欲 +0.15；`content["type"]="creation"` → 创造欲不动（不自循环）；`type` 缺失/其他值 → 创造欲不动
  - [ ] **run_eval**：
    - [ ] 四类型都低于 `peak_threshold` → `[]`，无 LLM 调用
    - [ ] 互动欲达峰（造 `value=0.9`）→ 1 次 LLM 调用（`output_type="desire"`）、`evaluator.evaluate` 被调 1 次（收到该 `LLMOutput`）、返回 1 个 `ShortTermDesire`（`type` 正确、`status is PENDING`、`strength == 0.9`、`description`/`goal` 来自 fixture）、该类型 `value` 重置为 0、发布 `desire_generated`（`content["desire_id"] == desire.id`）
    - [ ] **只生成最迫切的 1 个**：互动欲 0.95 + 探索欲 0.92 都达峰 → 只生成互动欲；探索欲 `value` 保留 0.92 不重置
    - [ ] **长期加压**：seed 一个 `type=EXPLORATION` 的长期欲望 → 探索欲 `value` 额外 +0.1
    - [ ] **疲惫加压**：`run_eval(energy=ENERGY_REST_THRESHOLD - 1)` → 休息欲 `value` +0.1；`run_eval(energy=ENERGY_REST_THRESHOLD)`（不疲惫）→ 休息欲不动
    - [ ] **衰减**：`updated_at` 设为 1 天前 → `value` 衰减 `value_decay × 1`
    - [ ] **抑制门控**：`suppression_threshold=0.95 > value=0.92`（达峰但被抑制）→ 不生成，返回 `[]`
    - [ ] **SUPPRESSED 释放**：SUPPRESSED 欲望其类型 `value=0.6 >= suppression=0.5` → `run_eval` 后该欲望 `status is PENDING`（不新生成）；`value=0.4 < 0.5` → 保持 SUPPRESSED
    - [ ] **主题种子**：seed 探索型长期欲望（`subtopics=["骑士团", "大学朋友"]`）+ 记忆命中「骑士团」→ LLM 收到的 prompt 含「大学朋友」、不含「骑士团」（没做过优先），且 `goal.topic` 被钉死为「大学朋友」（LLM 返回「骑士团」被覆盖）；无 subtopics（无 seed）→ `goal.topic` 清空为 `None`；有 seed 但 LLM 返回 `goal:null` → 不合成 goal（保持 `None`）
    - [ ] **去重**：① 话题锚点——seed 钉进 `goal.topic` 后，与已有 PENDING 的 `goal.topic` 精确相等 → 丢弃（不入队、不发布 `desire_generated`），异 topic → 保留；② 余弦兜底——注入 fake embed（同 description 返回同向量）→ 新欲望与已有 PENDING 语义重复被丢弃；正交向量 → 正常入队；`embed=None` / embed 抛异常 → 不去重
  - [ ] **satisfy**：
    - [ ] `goal_met=True` → `status is SATISFIED`、表达权重 +0.05、长期进度 +0.1、发布 `desire_satisfied`
    - [ ] **goal 精确计数**：`goal.count=3` → 前两次 `goal_met=True` 累计 `goal_progress` 保持 PENDING、不发布；第三次 → SATISFIED + 发布 `desire_satisfied`
    - [ ] **最相关回写**：同类型两条长期欲望（subtopics 各不同）+ `goal.topic` 命中第二条 → 只回写第二条 progress、第一条不动
    - [ ] `goal_met=False` 且 `retry_count <= retry_limit` → `retry_count+1`、`status` 仍 `PENDING`、无事件
    - [ ] `goal_met=False` 且 `retry_count > retry_limit` → `status is EXPIRED`、值回增 `+REFUND_DELTA`、抑制阈值 +0.1、发布 `desire_expired`
  - [ ] **expire**：`status is EXPIRED` + 值回增 + 抑制阈值上浮 + 发布 `desire_expired`
  - [ ] **satisfy/expire 未命中**：`desire_id` 不存在 → 无事件、不抛
  - [ ] **mark_active / mark_suppressed**：
    - [ ] `mark_active`：PENDING → ACTIVE；SUPPRESSED/SATISFIED/EXPIRED/缺失 → 不变（no-op）
    - [ ] `mark_suppressed`：ACTIVE → SUPPRESSED；PENDING/SATISFIED/EXPIRED/缺失 → 不变（no-op）
    - [ ] `satisfy` 释放：ACTIVE 欲望 `satisfy` 未达标 → `status is PENDING`（不卡 ACTIVE）；达标 → SATISFIED
  - [ ] **facade**（`test_desire_facade.py`）：
    - [ ] `add_value(OBSERVATION_STATE)` → 互动欲加压；`add_value(ACTIVITY_END)`（content 含 `desire_id`+`goal_met`）→ 满足回写；`add_value(ACTIVITY_END)`（缺键/错类型）→ 无操作
    - [ ] `evaluate` / `get_pending` / `get_all` / `satisfy` / `expire` 委托（`get_all` 返回 `DesireState` 三字段非空；`short_term` 含 satisfied 历史、`long_term` 含 seed 的长期欲望）
    - [ ] `add_long_term(desire)` → `list_long_term` 多一条、字段全等（委托 `insert_long_term`）
    - [ ] `pressure_creation(delta)` 委托 → 创造欲 `value` 加压 `delta`
    - [ ] `mark_active` / `mark_suppressed` 委托 → `status` 依次 ACTIVE / SUPPRESSED
- [ ] 集成测试：无（LLM 全 mock、DB 用 `:memory:`；与 activity/expression 的真实编排归 09-activity/11-expression）
- [ ] E2E 测试：无

## 完成定义

- [ ] `ruff check` 零报错
- [ ] `pyright` 零报错
- [ ] `pytest` 全绿
- [ ] `test-inventory.md` 已更新
- [ ] 组合根：`DesireStore(db)` → `DesireFacade(store, bus, llm, evaluator, config.desire, lambda: memory.list_memories(), embed)`；启动时 seed 四类型 `desire_value`（`default_value(t)` + `updated_at=now`）与 3 个初始长期欲望（canon §4，表空才 seed）；订阅 `OBSERVATION_STATE`/`ACTIVITY_END` 到 `facade.add_value`，CLOCK_TICK 的 `DESIRE_EVAL` 分发到 `facade.evaluate()`
- [ ] 09-activity 消费欲望走 `get_pending()`；09-activity 的 `activity_end` content 契约（`desire_id`/`goal_met`）与本 spec §技术方案一致；11-expression 搭话：用户回复时 `satisfy` 该互动欲（消费闭环）
