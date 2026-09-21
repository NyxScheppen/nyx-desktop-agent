# 内在生命系统事实摘要

> 本文件只记录当前源码已经实现的事实，供无关代码快速了解边界。
> 它不是契约，不替代唯一完整 spec：[`../specs/08-inner-life.md`](../specs/08-inner-life.md)。
> 如果本摘要、完整 spec 与源码不一致，先按完整 spec 的项目规则处理并同步修正文档；代码行为的最终事实仍以 `nyx/` 源文件为准。

## 入口与依赖

- Facade：`nyx/inner_life/facade.py:InnerLifeFacade`
- 子系统：`nyx/inner_life/reflection.py:Reflection`
- 持久化：`nyx/inner_life/store.py:InnerLifeStore`
- 纯函数：`nyx/inner_life/emotion.py`
- 共享类型：`nyx/types.py` 的 `CurrentState`、`SelfNarrative`、`Personality`、`Values`、`Aesthetic`、`ReflectionOutcome`
- 事件与数据库：`nyx/events/`、`nyx/db.py`
- 组合根：`nyx/app_context.py` 构造并注入 activity、desire、memory、bus、LLM、eval
- 启动 seed：`nyx/bootstrap.py:seed_inner_life`

依赖方向是 `InnerLifeFacade -> Reflection -> InnerLifeStore`。`Reflection` 不反向 import Facade，避免循环依赖。Facade 还读取 `ActivityFacade` 的当前活动、`DesireFacade` 的待处理欲望，反思读取 `MemoryFacade` 的近期记忆、近期有效事实和阅读计数。

## 持久化表

内在生命使用五张 `id='self'` 的单行表：

| 表 | 当前字段 | 源码读写 |
|---|---|---|
| `personality` | `openness`、`conscientiousness`、`extraversion`、`agreeableness`、`neuroticism` | `get_personality` / `upsert_personality` |
| `value_system` | `attitude_to_human`、`ai_identity_acceptance`、`altruism`、`optimism` | `get_values` / `upsert_values` |
| `aesthetic` | `ornate`、`lyrical`、`classical`、`somber` | `get_aesthetic` / `upsert_aesthetic` |
| `energy` | `value`、`state` | `get_energy` / `upsert_energy` |
| `self_narrative` | `identity`、JSON `story`、JSON `self_view`、JSON `becoming`、`updated_at` | `get_narrative` / `upsert_narrative` |

`InnerLifeStore` 的每个 CRUD 方法在一个 `db.lock` SQL 块内运行。若调用时数据库已经处在 `Database.transaction()` 中，store 不自行提交；否则该方法自己提交。store 方法不互相嵌套持锁调用。

未 seed 的单行表读取返回 `None`。Facade 的状态、叙事和情感事件路径遇到缺失 seed 会抛 `RuntimeError`，不会默默使用默认值。组合根启动时用 `seed_inner_life` 对空表补入初始值。

## 情感

- `valence` 范围 `[-1, 1]`，`arousal` 范围 `[0, 1]`。
- 情感不落库，保存在 `InnerLifeFacade._valence`、`_arousal` 和 `_emotion_updated_at`；进程重启后从 `(0, 0)` 开始。
- `decay_emotion` 按 `f=max(0, 1-rate*elapsed_days)` 将两轴同时乘以 `f`，当前 `EMOTION_DECAY_RATE=0.5`。
- 每次 `apply_event` 入口先结算衰减，再应用事件偏移；`get_state` 无事件调用也会惰性结算，但不发布 `EMOTION_UPDATE`。
- 当前偏移表：`OBSERVATION_STATE` 为 `(0.0, 0.0)`，`DESIRE_SATISFIED` 为 `(+0.2, +0.1)`，`ACTIVITY_END` 为 `(+0.1, -0.1)`，`REFLECTION` 为 `(0.0, -0.1)`。
- `vad_to_category` 先映射六种基础情绪：`neutral`、`happy`、`sad`、`angry`、`worried`、`shy`。
- `resolve_emotion` 再覆盖为八种最终情绪，优先级是：精力 `EXHAUSTED`/`DRAINED` -> `sleepy`；活动为 `IDLE_REFLECTION`/`FREE_EXPLORATION` -> `thinking`；否则使用基础情绪。

## 精力

- 精力持久化在 `energy` 表，数值范围由 Facade 夹在 `[0, 100]`。
- `energy_to_state` 分界为 `80/60/40/20`，对应 `energetic/okay/tired/exhausted/drained`。
- `ACTIVITY_END` 先按自上次能量更新时间做闲置恢复，再读取 `content["energy_delta"]` 加减；缺失或非数值按 `0`。
- 当前闲置恢复速率为每小时 `5.0`，保存在 `_energy_updated_at`。
- `get_state` 即使没有活动结束事件，也会执行闲置恢复并把最新值回写 `energy` 表。
- 能量写入和同一事件产生的情感事件可在同一数据库事务内提交。

## 事件消费与事务边界

统一入口是 `InnerLifeFacade.apply_event(event, consumer_id=None)`，组合根将 `OBSERVATION_STATE`、`DESIRE_SATISFIED`、`ACTIVITY_END`、`REFLECTION` 订阅到它。

- 没有 `consumer_id` 的兼容调用：在本地事务里执行状态变化并把派生事件写入事务；事务失败时恢复情感值和两个时间锚点快照；提交后再 `announce_committed`。
- 带 `consumer_id` 的 durable 调用：
  1. 先检查 `event_effect`；
  2. `REFLECTION` 的外部读取、LLM、eval、JSON 解析在事务外执行；
  3. 在同一本地事务中原子写 effect marker、状态变化和派生事件；
  4. 异常时恢复进程内情感/时间快照，让数据库事务回滚；
  5. 提交后唤醒派生事件。
- effect marker 使用 `(event.id, consumer_id)`，同一 durable 事件重复投递不会重复增加情感、扣精力或重复写反思结果。
- 派生事件通过 `append_in_transaction` 进入 `event_log`；提交后的广播失败不撤销已提交本地状态，后续恢复依赖总线投递/扫描机制。
- 每次情感事件最终会产生一个 `EMOTION_UPDATE`，载荷含 `valence`、`arousal`、最终 `emotion` 字符串，事件来源为 `INTERNAL`，沿用触发事件的 `correlation_id`。

## 惰性状态

`get_state` 的读取顺序是：

1. 读取 personality、values、aesthetic；缺失立即失败；
2. 结算内存情感衰减；
3. 读取并结算 energy；
4. 读取当前活动类型；
5. 按能量和活动覆盖基础情绪；
6. 读取 pending short-term desires；
7. 组装 `CurrentState`。

因此 `CurrentState` 是一次读取时刻的组合快照，不是数据库事务快照。多个 Facade 读取之间可能观察到不同时间点；当前系统依靠事件消费顺序和各自 store 事务维持局部一致。

## 反思

`Reflection` 是慢变量的唯一写入口，当前一次反思只调用一次 LLM；事实变化不额外触发第二次反思：

1. 事务外读取最近最多 20 条记忆、五类慢变量、当前长期欲望；
2. 构造反思 prompt（近期事实以独立资料段注入）；
3. 调 `LlmClient.complete(module='inner_life', output_type='reflection', json_mode=True)`；
4. 紧跟 `Evaluator.evaluate`；
5. 解析并校验 story、becoming、self_view、三类 delta、长期欲望候选；
6. 计算漂移、叙事去重、按新阅读数缩放审美漂移；
7. 在事务外通过 DesireFacade 批量预检长期欲望容量、名称和 embedding 去重，并记录长期欲望快照；
8. 在本地事务内写 personality、values、aesthetic、narrative，以预检计划确定性写长期欲望，并给创造欲加压；
9. 同一事务追加 `REFLECTION_DONE`，提交后广播。

当前规则：

- 每个性格、三观、审美维度每轮最多漂移 `±0.5`，最后夹到 `[1, 10]`。
- story/becoming 使用字符相似度阈值 `0.9` 去重；story 去重后返回 `story_is_new=False`，becoming 重复则不追加。
- `self_view` 合并旧对象和新对象；`identity` 不变；`updated_at` 使用本轮时间。
- 审美漂移按 `count_new(MemoryKind.READING, narrative.updated_at)` / `3` 缩放，上限为 1。
- 长期欲望候选必须带 `linked_values`，只允许四个 `Values` 精确键；空数组合法，重复键稳定去重，非法关联键会使该候选被跳过。
- 候选先做结构过滤，再由欲望 Facade 按容量和名称/语义去重接纳，重复或坏候选不占后继有效候选的容量名额。
- 单个候选非法只记日志并跳过；核心反思字段仍可提交。`long_term_desires` 缺失/`null` 按空数组，字段非数组则整次失败。
- 长期欲望 embedding 失败或预检后快照变化会使整次提交失败并回滚，交由 durable delivery 重试；事务内不执行 embedding，也不嵌套开启欲望事务。
- 反思 JSON 非法、LLM 失败、eval 失败或本地事务失败都会抛出，不会写成功 effect，供 durable delivery 重试。
- 事务回滚不会撤销已经发生的 LLM/eval 调用，所以重试依靠 effect marker 和后续幂等写入。

## 当前限制与导航

- 情感是进程内状态，重启后不恢复历史情绪。
- `get_state` 是惰性组合读取，不提供跨表快照事务。
- 反思产出前没有持久化“已生成但未提交”的 plan；外部调用失败只能依赖 durable delivery 重试。
- 内在生命没有单独配置段；情感衰减、精力恢复、反思漂移等阈值目前是模块常量，长期欲望容量从 `DesireConfig` 传入。
- prompt 中当前仍直接展示 personality、values、aesthetic 数值；人格自然语言化、知识边界、轻量意图与回复失败降级属于新的表达系统更新 spec，尚未实现。

相关完整契约：

- 内在生命：[`../specs/08-inner-life.md`](../specs/08-inner-life.md)
- 模块总线：[`module-bus-system-facts.md`](module-bus-system-facts.md)、[`../specs/04-module-bus-system.md`](../specs/04-module-bus-system.md)
- 表达系统事实摘要：[`expression-system-facts.md`](expression-system-facts.md)
- 表达系统唯一完整契约：[`../specs/11-expression.md`](../specs/11-expression.md)
