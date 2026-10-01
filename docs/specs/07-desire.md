# 欲望系统（desire）：值机制、store、全周期与门面

> 范围：`desire/value.py`（值机制纯函数与常量）、`desire/store.py`（`DesireStore` 三类领域表 + 恢复 marker CRUD）、`desire/lifecycle.py`（`DesireLifecycle` 全周期编排）、`desire/facade.py`（`DesireFacade` 门面）。
> 值机制负责压力值、表达权重、抑制阈值的数学语义；生命周期负责加压/衰减/达峰生成/满足/淘汰；纯 CRUD 在 `store.py`；`facade.py` 是薄门面（事件入口 + 读委托）。
> spec 只定义契约（签名 + 数学语义 + 全周期编排语义 + 阈值/增量决策）；实现以 `nyx/desire/value.py` / `nyx/desire/store.py` / `nyx/desire/lifecycle.py` / `nyx/desire/facade.py` 源文件为准。

## 元信息

- **前置依赖**：01-types（`DesireType` / `DesireStatus` / `DesireValue` / `ShortTermDesire` / `LongTermDesire` / `Goal` / `DesireState` / `GoalAction` / `Event` / `EventType` / `Source`）、02-config（`DesireConfig`：`peak_threshold` / `retry_limit` / `long_term_capacity` / `short_term_capacity` / `value_decay`）、03-llm（`LlmClient.complete`）、04-module-bus-system（`Database`、`EventBus`、欲望表、`desire_generation_attempt` / `desire_eval_applied`）、10-eval（`Evaluator`）
- **本 spec 带来的连锁改动（ripple，已同步）**：01-types 给 `LongTermDesire` 加 `type` 字段、`DesireValue` 加 `updated_at` 字段；04-module-bus-system 给 `long_term_desire` 加 `type` 列、`desire_value` 加 `updated_at` 列；tech-ref 补 `desire/value.py` 与 `desire/store.py`；本轮为 `DesireConfig` 增加 `short_term_capacity`，并为 `long_term_desire.name_normalized` 建唯一索引。

### 当前实现状态

压力、短期欲望全生命周期、长期欲望候选、`linked_values` 回填、父子溯源、
`strength` 驱动力与行动优先级、活动消费和 durable tick 幂等均已实现；
`linked_values` 仍只持久化，尚未接入决策。

## 用户故事

> 作为 Nyx 系统的开发者，我想要 `DesireFacade` 把欲望全周期（观察加压、达峰生成、满足/淘汰回写）统一成一个门面，以便 `activity` 只调 `get_pending` 消费、`inner_life`/`activity` 只靠事件回写满足、仪表盘只调 `get_all` 快照；值机制纯函数收口在 `value.py`，领域与恢复状态 CRUD 收口在 `store.py`，全周期编排在 `lifecycle.py`，所有 LLM 调用和事件发布走可注入的 `llm` / `bus`。

## 验收标准

- [ ] `store.py` 含 `DesireStore`（`add_desire` / `get_desire` / `list_pending` / `list_suppressed` / `list_short_term` / `update_desire` / `get_value` / `list_values` / `upsert_value` / `apply_value_delta` / `reset_value_if_unchanged` / `try_mark_eval_applied` / `claim_for_activity` / `trim_pending` / `insert_long_term_if_available` / `insert_long_term` / `list_long_term` / `update_long_term` / `add_long_term_subtopics` / 生成尝试 CRUD）+ 序列化 helper（实现见 `nyx/desire/store.py`）
- [ ] `lifecycle.py` 含 `DesireLifecycle`（`pressure_from_observation` / `pressure_creation` / `satisfy_from_activity_end` / `run_eval` / `satisfy` / `expire` / `mark_active` / `mark_suppressed`）+ `_parse_desire` / `_pick_parent_long_term` / `_subtopic_freshness` / `_pick_topic_seed` / `_build_desire_prompt`（实现见 `nyx/desire/lifecycle.py`）
- [ ] `facade.py` 含 `DesireFacade`，公开方法包括：`add_value(source: Event, consumer_id: str | None = None) -> None` / `evaluate(energy: float = 100.0, event_id: str | None = None) -> list[ShortTermDesire]` / `pressure_creation(delta: float) -> None` / `get_pending() -> list[ShortTermDesire]` / `get_all() -> DesireState` / `satisfy(desire_id: str, goal_met: bool) -> None` / `expire(desire_id: str) -> None` / `mark_active(desire_id: str) -> None` / `mark_suppressed(desire_id: str) -> None` / `release_active(desire_id: str) -> None` / `claim_for_activity(desire_id: str) -> bool` / `claim_for_activity_in_transaction(desire_id: str) -> bool` / `add_long_term_subtopics(desire_id: str, topics: list[str]) -> bool` / `prepare_long_term_candidates(desires: tuple[LongTermDesire, ...]) -> tuple[tuple[LongTermDesire, ...], LongTermSnapshot]` / `add_prepared_long_terms_in_transaction(desires: tuple[LongTermDesire, ...], snapshot: LongTermSnapshot) -> None`
- [ ] `add_value` 是**事件入口**（对 tech-ref「加压」注释的精确化）：`OBSERVATION_STATE` → 互动欲加压，`ACTIVITY_END` → 解析满足信号回写；其余类型忽略
- [ ] `run_eval`：先四类型衰减（`elapsed_days` 来自 `updated_at`）→ 长期欲望周期加压 → 疲惫加压（`energy < ENERGY_REST_THRESHOLD` → 休息欲 +`_REST_PRESSURE_DELTA`）→ 达峰判定（`at_peak and is_expressible`）→ **只生成最迫切的 1 个**（value 最高）→ LLM 生成 → 重置该类型 value → 入队 → 发布 `desire_generated`；无达峰返回 `[]`，非选中类型**保留压力**（不重置）
- [ ] `_parse_desire(raw)` 只接受 LLM 负责的非空 `description` 与正整数 `count`；系统另行按欲望类型决定 goal 是否存在，探索=`read`、创造=`write`、互动=`observe`，休息欲固定 `goal=None` 并忽略 count；topic 完全由系统提供。
- [ ] `satisfy(goal_met=True, goal=None)`：出队（`SATISFIED`）+ 表达权重正强化 + 长期进度回写 + 发布 `desire_satisfied`
- [ ] `satisfy(goal_met=True, goal 非 None)`：`goal_progress+1` 累计；`>= goal.count` 才满足（出队 + 强化 + 回写 + 发布），否则保持 `PENDING`（累计进度，不重复满足）
- [ ] `satisfy(goal_met=False)`：`retry_count+1`；`> retry_limit` → 放弃（`EXPIRED` + 值回增 + 抑制阈值上浮 + 发布 `desire_expired`）；否则保持 `PENDING`（`created_at` 不变，`list_pending` 的 `created_at ASC` FIFO 天然靠前，无显式插队动作）
- [ ] `claim_for_activity`：使用条件更新原子完成 `PENDING → ACTIVE`，仅一次调用成功；活动 starter 在同一数据库事务中完成领取和活动插入，任一步失败整体回滚。
- [ ] `mark_active`：保留兼容入口；真实活动已通过 claim 领取时为幂等 no-op
- [ ] `mark_suppressed`：`ACTIVE → SUPPRESSED`（活动中断/异常停车，不立即重试），仅 ACTIVE 可转、其余幂等 no-op
- [ ] `run_eval` 释放：`SUPPRESSED` 欲望其类型仍可表达（`is_expressible`）→ `PENDING` 放回队列；不可表达保持 `SUPPRESSED`
- [ ] `expire`：`EXPIRED` + 值回增 + 抑制阈值上浮 + 发布 `desire_expired`
- [ ] `prepare_long_term_candidates` 只能在事务外调用；它对反思产生的整批候选执行容量、规范化同名和语义去重，并同时过滤候选之间的重复，返回获准候选与基于现有长期欲望 `id/name/description` 的 `LongTermSnapshot`。`embed=None` 时只做确定性名称去重；embedding 启用后任一 embedding 失败则整批预检失败。反思是运行时新增长期欲望的唯一入口。
- [ ] `add_long_term_subtopics(desire_id, topics)` 只允许给已存在的源长期欲望追加去空白、稳定去重后的子主题；父记录不存在或 topics 为空时返回 `False`，不得创建长期欲望或改写其他记录。
- [ ] `add_prepared_long_terms_in_transaction` 只能在调用方已开启的事务中执行，且不调用 embedding；存在获准候选且当前快照与预检快照不一致时抛 `RuntimeError`，由上层 durable delivery 重算。没有获准候选时直接 no-op，避免无关长期欲望变化阻塞核心反思；快照一致时逐条调用 `insert_long_term_if_available`，最终容量或名称守卫未通过的候选直接跳过，不淘汰已有欲望。
- [ ] 事件发布遵守「Facade 自己 publish、绝不返回 Event」；事件 `source=INTERNAL`；`desire_satisfied` / `desire_expired` 的 `correlation_id` = `desire.id`
- [ ] `run_eval` 的 LLM 产出（`output_type="desire"`）后紧跟 `await evaluator.evaluate(output)`（漏记由测试断言兜底）；解析成功的产出先保存到 `desire_generation_attempt`，正式提交失败后的重试必须复用该 JSON，不重复调用 LLM。
- [ ] `run_eval` 的本地状态使用评估锁和 `updated_at` 条件重置；评估期间发生的新压力不得被旧快照覆盖。
- [ ] durable tick 传入 `event_id` 时，周期衰减/压力/SUPPRESSED 释放与 `desire_eval_applied` marker 同事务提交；同一 tick 重试继续生成阶段但不重复结算周期状态。兼容直调 `event_id=None` 时每次照常结算。
- [ ] `run_eval` 生成后按 `DesireConfig.short_term_capacity` 裁剪待消费短期欲望；行动优先级统一为 `short_term.strength * type.expression_weight`，同分按 `created_at ASC, id ASC`。被容量裁剪的记录在同一事务内按该优先级增加其父长期欲望 strength；若新记录被裁剪，不发布 `desire_generated`。
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
- `expression_weight` 是表达权重，满足一次后正强化；与短期 strength 相乘形成行动优先级。
- `suppression_threshold` 是习得性抑制阈值，失败或抑制后上浮。初始值 `0.5` 低于默认达峰阈值，因此初始状态下达峰即可表达；多次失败后阈值可能高于达峰阈值，达峰也可能暂不表达。
- `decay_value` 的 `elapsed_days` 由生命周期根据 `desire_value.updated_at` 计算：`(now - updated_at) / 86400`。每次评估先衰减并结算，再将 `updated_at` 更新为当前时间；两次评估之间不实时下降。
- 加压增量的来源和具体值由生命周期定义，不由纯函数层猜测：观察 `+0.15`、每条长期欲望周期 `+0.1 * strength`、疲惫 `+0.1`、读书/自由探索结束时创造欲 `+0.15`。`strength=0` 的长期欲望不加压也不参与短期父对象选择。
- 回增统一调用 `apply_pressure(value, REFUND_DELTA)`，不额外引入回增函数。
- `action_priority(desire, expression_weight) = desire.strength * expression_weight`，结果夹在 `[0,1]`；待消费排序与容量裁剪必须使用同一公式，同分按 `created_at ASC, id ASC`。

## 技术方案

- **实现文件**：`nyx/desire/value.py`、`nyx/desire/store.py`、`nyx/desire/lifecycle.py`、`nyx/desire/facade.py`（无 API；`desire_eval_applied` 表结构由 04-module-bus-system 定义）
- **库**：无新库（标准库 `json` / `time` / `uuid` / `typing`；`aiosqlite` 已由 04-module-bus-system 引入）
- **公开面**：`from nyx.desire.value import ...`（本 spec 的值机制函数与常量）；`from nyx.desire.store import DesireStore`；`from nyx.desire.lifecycle import DesireLifecycle`；`from nyx.desire.facade import DesireFacade`（不加 `__all__`；序列化 helper 私有）
- **四层职责**：`DesireFacade`（Facade）→ `DesireLifecycle`（全周期编排）→ `DesireStore`（领域表与恢复 marker CRUD）；`value.py` 是被 lifecycle/store 直接调用的纯函数模块，不新增运行时抽象层。`lifecycle` 由 `facade` 内部构造（共享 store），让 `facade` 只做事件入口 + 读委托
- **store 锁约定（同 07）**：每个方法一个 `async with self._db.lock` 的 SQL 块；store 方法之间不互相调用对方的持锁方法（`asyncio.Lock` 不可重入）
- **两个读路径（`get_pending` vs `get_all`）**：`get_pending` 只含 `PENDING` 且 `strength > 0` 的记录，Facade 结合当前类型表达权重按行动优先级排序，供活动、主动搭话和 prompt 消费；去重也忽略零强度短期记录，避免无驱动力记录阻挡新欲望。`get_all` 返回含终态和零强度记录的完整快照，供 `/api/desires` 仪表盘。
- **可空 JSON 列（同 07 的 `embedding`）**：`short_term_desire.goal` 是 `Goal | None` ⟺ `goal TEXT` 可空，`None ↔ SQL NULL`（非 `"null"` 字符串）
- **`add_value` 是事件入口（决策，对 tech-reference 注释的精确化）**：tech-ref 写「活动/对话/长期欲望 加压」，但 ROUTING 里 desire 订阅了 `OBSERVATION_STATE` 和 `ACTIVITY_END` 两个事件——`OBSERVATION_STATE` 是加压、`ACTIVITY_END` 是满足回写（`04-module-bus-system` 的路由与事件语义、ROUTING 注释「满足」）。故 `add_value` 按 `source.type` 派发；组合根用 `bus.subscribe(EventType.OBSERVATION_STATE, facade.add_value)` + `bus.subscribe(EventType.ACTIVITY_END, facade.add_value)` 绑定
- **`evaluate()` 由 tick 触发**：TICK_ROUTING 的 `DESIRE_EVAL → desire`。runtime 不把完整 Event 传入 Facade，只把 `event.id` 作为 `event_id` 传给 `evaluate()`，用于周期状态幂等；兼容直调可省略。`desire_generated` 的 `correlation_id = desire.id`，不改成 tick correlation。
- **加压增量（默认值，标注可推翻）**：`_OBSERVATION_PRESSURE_DELTA=0.15`、每条长期欲望周期增量 `0.1 * strength`、`_REST_PRESSURE_DELTA=0.1`、`_CREATION_ACTIVITY_PRESSURE_DELTA=0.15`。同类型多条长期欲望累加；strength 为 0 时跳过该条长期压力（周期衰减仍按原规则结算并更新时间戳）。
- **衰减时机（决策：加 `updated_at` 列，已与用户确认）**：`elapsed_days = (now - updated_at) / 86400`，`decay_value(value, elapsed_days, config.value_decay)`。`updated_at` 记录"最后一次 value 变化"，每次 evaluate 先衰减结算再写回 `updated_at = now`；衰减是单调的，两次 evaluate 之间 value 不实时下降（与 06-memory-system 的 `decay_freshness` 一样属于按访问结算的当前实现限制），相对顺序不破坏
- **达峰生成（决策：只生成最迫切 1 个，已与用户确认）**：达峰判据 = `at_peak(value, peak) and is_expressible(value, suppression)`（本 spec 值机制的门控组合）；多个达峰类型时 `max(..., key=value)` 取最高者生成 1 个，**只重置选中类型**，其余达峰类型保留压力下次 evaluate 再生成——每次 evaluate 最多 1 次 LLM 调用（原则 1）
- **去重（decision，可推翻）**：`run_eval` 生成后、入队前两步判定——① **话题锚点优先**：新欲望 `goal.topic` 非 None 时，与 `list_pending()` 各待消费欲望的 `goal.topic` 精确相等即判重复丢弃（确定性、零误判、不依赖 embedding）；② **余弦兜底**：`goal.topic` 缺失（None）或未命中时，用注入的 `EmbedFn`（`memory/retrieval` 的 `build_embed`，与 memory/evaluator 共享同一实例）算新欲望 `description` 的 embedding，与 `list_pending()` 各 description embedding 做 `cosine` 比对，任一 `>= _DEDUP_SIM_THRESHOLD(0.9)` 判语义重复丢弃（不入队、不发布，value 已在重置步骤归零）。`embed=None`（向量层禁用）或 embed 抛异常降级为不去重（best-effort 旁路，同矛盾检测）
- **父长期欲望与主题**：选定达峰类型后，只在同类型、`strength > 0` 的长期欲望中按 `strength DESC, created_at ASC, id ASC` 选父对象。topic 从父对象子主题池按「没做过优先、否则 freshness 最低」选择；子主题池为空则用父名称；没有合格父对象则 topic 和 `parent_long_term_id` 都为 `None`。系统据类型构造 action/topic/goal，LLM 只生成 description/count。
- **`strength` 语义**：`ShortTermDesire.strength` 是达峰压力快照；`LongTermDesire.strength` 是 `[0,1]` 驱动力。容量裁剪时父 strength 增加 `action_priority`，最终满足时父 strength 减少同一公式并推进 progress。部分 goal 完成、失败、过期、抑制、去重丢弃均不改父 strength；旧数据或父记录缺失时不得猜父对象。
- **长期欲望初始化与唯一新增入口**：bootstrap 在空表时写 3 条 canon 初始数据；运行时只有 08-inner-life 反思可新增长期欲望。09-activity 探索只能向短期欲望明确记录的父长期欲望追加子主题。
- **反思批量接入**：08-inner-life 在事务外调用 `prepare_long_term_candidates`，把获准候选与 `LongTermSnapshot` 放入 `ReflectionPlan`；事务内调用 `add_prepared_long_terms_in_transaction`。`LongTermSnapshot` 只包含影响新增去重的 id/name/description，因此并发 strength、progress、subtopics 变化不制造无关冲突。
- **五态流转（V2，`ACTIVE`/`SUPPRESSED` 纳入）**：`PENDING → ACTIVE` 由 `claim_for_activity` 原子领取；`ACTIVE → SATISFIED | EXPIRED`（满足时从 ACTIVE 释放并结算）；`ACTIVE → SUPPRESSED`；`SUPPRESSED → PENDING`（`run_eval` 里类型仍可表达即释放回队列）。`SUPPRESSED` 可逆、非终态；续做路径恢复同一记录时由活动完成结算，不重复领取。
- **`activity_end` 的满足信号契约（09-activity 引用）**：`event.content` 含 `desire_id`（`str | None`）与 `goal_met`（`bool | None`）。`satisfy_from_activity_end` 缺任一键或非预期类型即跳过（不抛），因为观察用户/发呆等活动无欲望可满足。额外：`event.content["type"]` 为 `reading` / `free_exploration`（`ActivityType.value`）时，满足逻辑之外再给创造欲加压 `_CREATION_ACTIVITY_PRESSURE_DELTA`（创作活动 `creation` 结束不自循环；`type` 缺失/其他值跳过）
- **新增 `output_type="desire"`**：`LLMOutput.type` 自由字符串，开放集合新增无冲突

## 对象完整性

### `ShortTermDesire`

**入口清单**

| 入口 | 产生条件 | 写入位置 |
|---|---|---|
| `DESIRE_EVAL` | 类型压力达峰且可表达，LLM 的 description/count 合法 | `short_term_desire`；提交前先写 `desire_generation_attempt` |
| 生成 attempt 恢复 | 上次 LLM 已完成、正式事务失败 | 复用 attempt 的 type、topic、父 ID、原始输出，不重复调用 LLM |
| DB 迁移 | 升级已有数据库 | 旧短期欲望的 `parent_long_term_id` 为 `NULL` |

**消费者清单**

| 消费者 | 发现方式 | 用途 |
|---|---|---|
| Activity starter | `get_pending()` | 按行动优先级选择并原子 claim，父 ID复制到 activity progress |
| 主动搭话 | `get_pending()` 后筛互动类型 | 按同一优先级选择互动欲望 |
| expression / inner-life prompt | `CurrentState.active_desires` | 读取已排序待消费欲望 |
| 欲望结算 | `desire_id` 精确查询 | 按父 ID 回写长期进度和 strength |
| REST / 前端 | `get_all()` | 展示全量短期历史和父关系 |

**状态迁移表**

| 当前状态 | 条件 | 下一状态 | 副作用 / 失败落点 |
|---|---|---|---|
| 不存在 | 生成提交成功且容量保留 | `PENDING` | 写父 ID并发布 `DESIRE_GENERATED` |
| 不存在 / `PENDING` | 容量裁剪 | 删除 | 同事务按行动优先级增加父 strength；无父则只删除 |
| `PENDING` | claim 成功 | `ACTIVE` | 与活动插入同事务 |
| `ACTIVE` | 部分完成 | `PENDING` | 不改父 strength |
| `ACTIVE/PENDING` | 最终满足 | `SATISFIED` | 同事务降低父 strength、增加父 progress、强化表达权重 |
| `ACTIVE` | 中断/异常 | `SUPPRESSED` | 不改父 strength |
| `SUPPRESSED` | 类型恢复可表达 | `PENDING` | 不改父 strength |
| 非终态 | 超过重试或超时 | `EXPIRED` | 压力回灌和抑制上浮；不改父 strength |

**Bad case 表**

| 情况 | 处理 |
|---|---|
| 空 | 无达峰类型返回空；无父长期欲望时父 ID/topic 为 `None`，仍可生成系统 goal |
| 失败 | LLM/解析失败不入队；正式提交失败保留 attempt，重试复用父 ID/topic |
| 部分完成 | 只累加 goal_progress，不提前降低父 strength |
| 乱序 | claim 用条件更新；最终结算在事务内检查终态，旧事件不得覆盖终态 |
| 重放 | eval marker 防重复周期加压；满足/过期终态幂等；被裁剪记录已删除，不能二次回灌 |
| 删除 | 父长期欲望删除时外键置空；禁止按类型、topic 或最新记录猜父对象 |

### `LongTermDesire`

**入口清单**

| 入口 | 产生条件 | 写入位置 |
|---|---|---|
| bootstrap | 长期欲望表为空 | 3 条 canon 初始数据 |
| reflection | 反思候选通过结构、容量、名称和语义去重 | `long_term_desire` |
| exploration | 不适用：探索不得创建长期欲望 | 只对明确父 ID追加 subtopics |

**消费者清单**

| 消费者 | 发现方式 | 用途 |
|---|---|---|
| desire eval | 全量读取 | 按 `0.1 * strength` 加压；按 strength 选择短期父对象 |
| reflection prompt | 全量读取 | 展示名称、进度、数值 strength 和文字强度 |
| 短期结算/裁剪 | `parent_long_term_id` 精确查询 | 调整 progress/strength |
| exploration | activity progress 中的父 ID | 幂等追加子主题 |
| REST / 前端 | `get_all()` | 展示长期欲望状态 |

**状态迁移表**

| 当前状态 | 条件 | 下一状态 | 副作用 / 失败落点 |
|---|---|---|---|
| 不存在 | bootstrap 或 reflection 接纳 | 存在 | 初始 strength=0.5、progress=0 |
| 存在 | 短期满足 | 存在 | strength 减行动优先级、progress +0.1，均夹范围 |
| 存在 | 子短期被容量裁剪 | 存在 | strength 加行动优先级，夹到 1 |
| 存在 | 探索产生后续主题 | 存在 | 稳定去重追加 subtopics |
| strength=0 | 周期评估 | 存在且无驱动力 | 不加压、不被选为新短期父对象 |

**Bad case 表**

| 情况 | 处理 |
|---|---|
| 空 | 无长期欲望时仍允许其他压力产生短期欲望，但父 ID/topic 为空 |
| 失败 | 反思候选失败沿既有 durable retry；子主题写失败使探索活动失败并由 checkpoint 重试 |
| 部分完成 | 短期 goal 未完成前不调整 strength；探索子主题已写但知识未写时可重放，子主题追加与知识沉淀各自幂等 |
| 乱序 | strength 反馈与对应的满足/删除处于同一 DB 事务；反思快照不包含 strength/subtopics，避免无关冲突 |
| 重放 | 满足终态、容量删除和子主题稳定去重分别吸收重放 |
| 删除 | 当前无公开删除入口；若内部删除，子短期与 attempt 父 ID由外键置空 |

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
    - [ ] `add_desire + get_desire` 往返：含 `goal=Goal(READ, 3, "骑士团")`、`parent_long_term_id`、非默认 `retry_count`/`status` → 各字段全等
    - [ ] `goal=None` 往返 → `get_desire().goal is None`（SQL NULL 非 `"null"` 字符串）
    - [ ] `list_pending`：造 pending/active/satisfied/expired 各一条 → 只返回 pending，按 `created_at ASC` 排序
    - [ ] `list_suppressed`：造 suppressed 两条（`created_at` 乱序）+ pending/active 各一条 → 只返回 suppressed，按 `created_at ASC` 排序
    - [ ] `list_short_term`：同上四条 → 返回全部（含 satisfied/expired），按 `created_at DESC` 排序（区别于 `list_pending` 的过滤 + ASC）
    - [ ] `update_desire`：改 `status`/`retry_count` → `get_desire` 验证
    - [ ] `goal_progress` 往返：`add_desire` 带 `goal_progress=2` → `get` 往返；`update_desire` 改 `goal_progress=3` → 再 `get` 验证（goal 精确计数存储层）
    - [ ] `list_values + upsert_value`：`upsert_value` 新建 → `list_values` 返回；同 `type` 再 `upsert_value` 改 `value`/`updated_at`（ON CONFLICT 更新不重复建行）
    - [ ] `insert_long_term + list_long_term + update_long_term`：`subtopics`/`linked_values` JSON 数组往返、`type` 枚举往返；`update_long_term` 改 `progress`/`strength`
    - [ ] `trim_pending`：按行动优先级保留、同分 FIFO；被删除项按同一优先级增加显式父对象 strength，父缺失时只删除
    - [ ] `add_long_term_subtopics`：去空白、稳定去重；空输入或父对象不存在返回 `False`
  - [ ] **lifecycle 纯函数**：
    - [ ] `_parse_desire`：合法 JSON → `(description, count)`；缺失/空白 description、非正整数 count、非对象 JSON → `ValueError`；模型额外输出 action/topic/goal 不进入领域对象
    - [ ] `_subtopic_freshness`：空串/纯空白 → `None`（通配符不做匹配）；非空命中 → 最新 freshness
    - [ ] `_pick_topic_seed`：空池 → `None`；全没做过（无命中记忆）→ 第一个；部分做过 → 取没做过的；都做过 → 取新鲜度最低者
    - [ ] `_pick_parent_long_term`：只选同类型且 `strength>0`，按 `strength DESC, created_at ASC, id ASC`
    - [ ] `_build_desire_prompt`：含类型 `.value` 与种子；`seed=None` → 含「（无）」
  - [ ] **pressure_from_observation**：互动欲 `value` 由 `x` → `min(1.0, x + 0.15)`；`updated_at` 更新
  - [ ] **pressure_creation**：创造欲 `value` 由 `x` → `min(1.0, x + delta)`（传 `delta=0.2`）；`updated_at` 更新
  - [ ] **satisfy_from_activity_end 活动结束加压**：`content["type"]="reading"` → 满足逻辑外创造欲 +0.15；`content["type"]="free_exploration"` → 创造欲 +0.15；`content["type"]="creation"` → 创造欲不动（不自循环）；`type` 缺失/其他值 → 创造欲不动
  - [ ] **run_eval**：
    - [ ] 四类型都低于 `peak_threshold` → `[]`，无 LLM 调用
    - [ ] 互动欲达峰（造 `value=0.9`）→ 1 次 LLM 调用（`output_type="desire"`）、`evaluator.evaluate` 被调 1 次；系统按类型构造 action/goal/topic，模型只提供 description/count；该类型 `value` 重置为 0、发布 `desire_generated`
    - [ ] **只生成最迫切的 1 个**：互动欲 0.95 + 探索欲 0.92 都达峰 → 只生成互动欲；探索欲 `value` 保留 0.92 不重置
    - [ ] **长期加压**：每条长期欲望给对应类型增加 `0.1 * strength`；strength=0 不加压、不参与父选择
    - [ ] **疲惫加压**：`run_eval(energy=ENERGY_REST_THRESHOLD - 1)` → 休息欲 `value` +0.1；`run_eval(energy=ENERGY_REST_THRESHOLD)`（不疲惫）→ 休息欲不动
    - [ ] **衰减**：`updated_at` 设为 1 天前 → `value` 衰减 `value_decay × 1`
    - [ ] **抑制门控**：`suppression_threshold=0.95 > value=0.92`（达峰但被抑制）→ 不生成，返回 `[]`
    - [ ] **SUPPRESSED 释放**：SUPPRESSED 欲望其类型 `value=0.6 >= suppression=0.5` → `run_eval` 后该欲望 `status is PENDING`（不新生成）；`value=0.4 < 0.5` → 保持 SUPPRESSED
    - [ ] **父对象与主题**：最强同类型长期欲望成为父对象；优先取未使用/最陈旧子主题，空子主题池用父名称；无合格父时父 ID/topic 为 `None`；attempt 恢复复用原父 ID/topic
    - [ ] **去重**：① 话题锚点——seed 钉进 `goal.topic` 后，与已有 PENDING 的 `goal.topic` 精确相等 → 丢弃（不入队、不发布 `desire_generated`），异 topic → 保留；② 余弦兜底——注入 fake embed（同 description 返回同向量）→ 新欲望与已有 PENDING 语义重复被丢弃；正交向量 → 正常入队；`embed=None` / embed 抛异常 → 不去重
  - [ ] **satisfy**：
    - [ ] `goal_met=True` → `status is SATISFIED`、表达权重 +0.05、显式父长期进度 +0.1、父 strength 减去强化前的行动优先级、发布 `desire_satisfied`
    - [ ] **goal 精确计数**：`goal.count=3` → 前两次 `goal_met=True` 累计 `goal_progress` 保持 PENDING、不发布；第三次 → SATISFIED + 发布 `desire_satisfied`
    - [ ] **显式父回写**：topic 即使命中另一条，也只回写 `parent_long_term_id` 指向的父对象；空/失效父 ID不猜测
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
    - [ ] `add_long_term_subtopics(desire_id, topics)` → 只向显式父对象稳定去重追加；空 topics 或父对象不存在返回 `False`
    - [ ] `prepare_long_term_candidates`：先过滤与现有欲望及批内候选的名称/语义重复，再按剩余容量接纳；embedding 严格失败；返回稳定快照
    - [ ] `add_prepared_long_terms_in_transaction`：事务外调用拒绝；事务内快照冲突抛错并不插入；快照一致时使用最终容量/名称守卫插入
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
