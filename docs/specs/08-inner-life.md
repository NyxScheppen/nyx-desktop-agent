# 内在生命（inner-life）：情感 + 精力 + 审美 + 反思 + 自我叙事

> 范围：`inner_life/store.py`（`InnerLifeStore` 五张单行表 CRUD）、`inner_life/emotion.py`（valence/arousal/8 档标签纯函数）、`inner_life/reflection.py`（`Reflection` 反思协调器）、`inner_life/facade.py`（`InnerLifeFacade`），以及审美维度在类型、迁移、记忆计数、prompt 和 seed 上的联动。
> 整块不拆一个 spec 写完（避免 facade↔reflection 成环）：`facade.py → reflection.py` 单向，reflection 不反 import facade；两者都依赖 `store.py`（叶子）。
> spec 只定义契约（签名 + 情感/精力/反思语义 + 阈值决策）；实现以 `nyx/inner_life/store.py` / `nyx/inner_life/emotion.py` / `nyx/inner_life/reflection.py` / `nyx/inner_life/facade.py` 源文件为准。

## 元信息

- **前置依赖**：01-types（`CurrentState` / `SelfNarrative` / `Personality` / `Values` / `Aesthetic` / `Event` / `EventType` / `Source` / `EnergyState` / `EmotionCategory` / `ActivityType` / `LongTermDesire` / `ReflectionOutcome`）、02-config（`Config` / `DesireConfig.long_term_capacity`）、03-llm（`LlmClient.complete`）、04-module-bus-system（`Database`、`EventBus`、`personality` / `value_system` / `aesthetic` / `energy` / `self_narrative` 五表）、06-memory-system（`MemoryFacade.list_memories`）、07-desire（`DesireFacade.get_pending` / `get_all` / `add_long_term` / `pressure_creation`）、09-activity（`ActivityFacade.get_current`）、10-eval（`Evaluator`）**
- **联动契约**：07-desire 提供 `add_long_term` / `pressure_creation`；04-module-bus-system 提供五张内在生命单行表及事件事务；06-memory-system 提供 `count_new(MemoryKind.READING, since)`；11-expression 消费 `CurrentState.aesthetic`；12-reading-system 产生 `kind='reading'` 记忆。
- **旧设计残留（已与用户确认删除）**：`VADCalibrator` / `AffinityMatrix` 不属于当前实现，本 spec 不实现，只实现 `vad_to_category`（valence/arousal → 8 档标签）。

## 用户故事

> 作为 Nyx 系统的开发者，我想要 `InnerLifeFacade` 把内在生命统一成一个门面——`apply_event` 按事件更新情感（衰减回基线 + 偏移）与精力、`reflect` 做慢变量反思（内部调 MemoryFacade/DesireFacade）、`get_state` 惰性结算并组装状态快照、`get_narrative` 读自我叙事——以便表达拼 prompt 用快照、前端面板看 valence/arousal/Big Five/三观/精力、反思是性格/三观/长期欲望/自我叙事的唯一演化入口。

## 验收标准

- [ ] `store.py` 含 `InnerLifeStore`（`get_personality` / `upsert_personality` / `get_values` / `upsert_values` / `get_aesthetic` / `upsert_aesthetic` / `get_energy` / `upsert_energy` / `get_narrative` / `upsert_narrative`）（实现见 `nyx/inner_life/store.py`）
- [ ] `emotion.py` 含 `clamp_valence` / `clamp_arousal` / `decay_emotion` / `apply_offset` / `event_offset` / `vad_to_category` / `resolve_emotion` + 常量（实现见 `nyx/inner_life/emotion.py`）
- [ ] `reflection.py` 含 `Reflection` / `ReflectionPlan` + `prepare` / `apply` / `run` + `drift_personality` / `drift_values` / `_drift_dim` / `_build_reflection_prompt` / `_parse_reflection` / `_validate_candidate` / `_to_long_term`（实现见 `nyx/inner_life/reflection.py`）
- [ ] `facade.py` 含 `InnerLifeFacade`（`apply_event` / `reflect` / `get_state` / `get_narrative`）+ `energy_to_state`，四个公开方法签名如上
- [ ] `vad_to_category` 只落 6 档（neutral/happy/sad/angry/worried/shy），`resolve_emotion` 补 sleepy/thinking 两档覆盖；优先级 **困倦 > 思考 > 情绪**
- [ ] `apply_event`：情感衰减（回基线 0,0）+ 事件偏移（`event_offset` 纯函数）；`ACTIVITY_END` 额外按 `energy_delta` 更新精力（含闲置恢复 + clamp + 重算档位）；`REFLECTION` 额外调 `reflect()`；每次情感变化发布 `EMOTION_UPDATE`（content 含 `valence`/`arousal`/`emotion`）；无 `consumer_id` 的兼容调用也在本地事务内执行，失败时恢复情感时间锚点
- [ ] `reflect()`：先在事务外读取近期记忆 + 当前慢变量并调用 **1 次 LLM**（`module="inner_life"`、`output_type="reflection"`、`json_mode=True`、`correlation_id` 透传自触发事件），再在一个本地事务内回写性格/三观/审美/叙事、长期欲望和创造欲；事务失败时全部本地写入回滚，LLM 不回滚，后续 durable delivery 重试；解析失败抛出 `ValueError`，不算消费成功，必须进入 delivery 重试。成功返回 `ReflectionOutcome`（`story`/`story_is_new`）；成功后发布 `REFLECTION_DONE`（content `{story, story_is_new}`，仅广播前端）
- [ ] `get_state()`：先惰性结算情感衰减和精力闲置恢复，再组装 `CurrentState`（情感内存 + 性格/三观/审美/精力 store + `current_activity`（`ActivityFacade.get_current()`）+ `active_desires`（`DesireFacade.get_pending()`））；精力结算后的值回写 `energy`；单行表未 seed → `RuntimeError`（fail-fast）
- [ ] 审美维度：`aesthetic` 为独立四轴慢变量（`ornate` / `lyrical` / `classical` / `somber`，范围 `[1,10]`），通过既有 `GET /api/state` 暴露并进入表达 system prompt；不新增独立 API 或事件
- [ ] 情感在内存不持久化；性格/三观/精力/自我叙事走 store；无 `VADCalibrator` / `AffinityMatrix`
- [ ] 事件发布遵守「Facade 自己 publish、绝不返回 Event」；事件 `source=INTERNAL`
- [ ] `reflect()` 的 LLM 产出（`output_type="reflection"`）后紧跟 `await evaluator.evaluate(output)`
- [ ] `pyright` strict 零报错

## 技术方案

- **实现文件**：`nyx/inner_life/store.py`、`nyx/inner_life/emotion.py`、`nyx/inner_life/reflection.py`、`nyx/inner_life/facade.py`、`nyx/types.py`、`nyx/db.py`、`nyx/memory/store.py`、`nyx/memory/facade.py`、`nyx/expression/prompt.py`、`nyx/main.py`
- **库**：无新库（标准库 `json` / `time` / `uuid` / `typing`；`aiosqlite` 已由 04-module-bus-system 引入）
- **公开面**：`from nyx.inner_life.store import InnerLifeStore`；`from nyx.inner_life.facade import InnerLifeFacade`；`from nyx.inner_life.emotion import (vad_to_category, resolve_emotion, decay_emotion, ...)`（不加 `__all__`）
- **三层**：`InnerLifeFacade`（Facade）→ `Reflection`（内部类，反思编排）→ `InnerLifeStore`（子系统，单行表 CRUD）。`Reflection` 由 `facade` 内部构造（共享 store），**不 import facade**（避免成环）；`facade.py` / `reflection.py` 都依赖 `store.py`（叶子）
- **store 锁约定（同 07）**：每个方法一个 `async with self._db.lock` 的 SQL 块；store 方法之间不互相调用对方的持锁方法（`asyncio.Lock` 不可重入）
- **五张单行表**：`personality`、`value_system`、`aesthetic`、`energy`、`self_narrative` 都用 `id='self'`（04-module-bus-system 固定键）。`get_*` 返回 `X | None`（未 seed 时 None）；所有读路径遇 None 抛 `RuntimeError`（"未初始化，组合根必须先 seed"），不静默兜底默认值
- **情感不持久化**：valence/arousal 在 `InnerLifeFacade` 内存字段（`self._valence` / `self._arousal` / `self._emotion_updated_at`），重启从基线（0,0）重启。没有 emotion 表（04-module-bus-system 无此表）
- **情感衰减（回基线，决策可推翻）**：`decay_emotion(v, a, elapsed_days, rate) = (v×f, a×f)`，`f = max(0, 1 - rate×elapsed_days)`，基线 = (0,0)。`EMOTION_DECAY_RATE=0.5`（每天回基线 50%）。`apply_event` 和 `get_state` 都会在各自入口先结算到当前时间；`get_state` 的惰性结算只更新内存，不发布 `EMOTION_UPDATE`
- **事件偏移 `event_offset` 纯函数**：`_OFFSETS` 表映射 4 个 inner_life 事件 → `(Δvalence, Δarousal)`；`OBSERVATION_STATE (0,0)`（观察不改，但触发衰减）、`DESIRE_SATISFIED (+0.2, +0.1)`（满足感）、`ACTIVITY_END (+0.1, -0.1)`（完成感+唤醒略降）、`REFLECTION (0, -0.1)`（反思平复）。数值是可推翻默认；`apply_offset` 施加后 clamp（valence `[-1,1]`、arousal `[0,1]`）
- **`vad_to_category` 6 档映射**：二维分区（阈值 `_V_NEAR=0.2` / `_A_LOW=0.3` / `_A_HIGH=0.6`）——低唤醒（`arousal<0.3`）：`valence>0.2`→shy、`<-0.2`→sad、否则 neutral；中高唤醒：`valence>0.2`→happy、`<-0.2`→（`arousal≥0.6`→angry 否则 worried）、否则 neutral。阈值是可推翻默认（分区语义按 01-types 各档注释）
- **`resolve_emotion` 8 档覆盖**：`energy_state ∈ {EXHAUSTED, DRAINED}` → sleepy（困倦最高优先级）；`current_activity ∈ {IDLE_REFLECTION, FREE_EXPLORATION}` → thinking（认知态）；否则 base。阈值（`_SLEEPY_STATES` / `_THINKING_ACTIVITIES`）是可推翻默认
- **精力模型**：`value ∈ [0,100]`、`energy_to_state` 五档映射（80/60/40/20 分界）。`ACTIVITY_END` 先做闲置恢复再加 `content.energy_delta`；`get_state` 无事件时也做惰性闲置恢复，并把结算值回写 `energy`。恢复速率 `_ENERGY_RECOVERY_PER_HOUR=5.0`/小时；重启后时间锚点从进程启动时重新开始
- **`ACTIVITY_END` content 契约（09-activity 引用）**：本 spec 消费两个键——`energy_delta`（`float`，精力变化，缺省 0）与 `desire_id`/`goal_met`（07-desire 已定义，本 spec 不读）。`desire_id`/`goal_met`/`energy_delta` 的完整形状由 09-activity 定义并保持与本 spec + 07 一致
- **反思 1 次 LLM（决策：已与用户确认）**：`Reflection.prepare` 在事务外拼近期记忆（`list_memories()[:20]` 摘要）+ 当前性格/三观/审美/叙事 + 现有长期欲望，调用一次 LLM 并解析为 `ReflectionPlan`；`Reflection.apply` 在调用方事务内执行确定性回写。`_parse_reflection` 非法时抛 `ValueError`，让 durable consumer 进入 retry；事务回滚不会撤销已经发生的 LLM/evaluator 调用
- **性格/三观漂移（decision 可推翻）**：`drift_personality` / `drift_values` 纯函数，每维 `base + clamp(delta, -_MAX_DRIFT, +_MAX_DRIFT)` 再 clamp 到 `[1,10]`；`_MAX_DRIFT=0.5`（每轮单维最多 ±0.5，慢漂移）。Big Five/三观范围 1-10（01-types 注释）
- **自我叙事回写**：`story`/`becoming` 是追加（`[..., 新条目]`）、`self_view` 是合并（`{**旧, **新}`）、`updated_at=now`；`identity` 不变
- **长期欲望候选**：`_parse_reflection` 校验每个候选 `{type, name, description, subtopics}`；`_to_long_term` 构造（`strength=_LONG_TERM_INIT_STRENGTH=0.5`、`progress=0.0`）；逐个 `desire_facade.add_long_term`，超出 `config.desire.long_term_capacity` 则停（反思侧截断候选数 + 07 的 `add_long_term` 内部容量/去重双保险）
- **审美维度**：`Aesthetic` 是四轴 `TypedDict`，每轴范围 `[1,10]`；`drift_aesthetic(base, delta)` 复用 `_drift_dim`，单维每轮最多漂移 `±0.5`。反思中的 `aesthetic_delta` 按 `min(new_reading_count / _AESTHETIC_MIN_READING, 1.0)` 缩放，`_AESTHETIC_MIN_READING=3`；`new_reading_count` 由 `MemoryFacade.count_new(MemoryKind.READING, narrative.updated_at)` 统计 `first_created_at > since` 的记忆，去重强化不算新增。
- **反思触发创造欲加压（ripple，07-desire 提供 `pressure_creation`）**：`reflect()` 成功后（LLM 产出 + 规则回写完成）调 `desire_facade.pressure_creation(_CREATION_REFLECTION_DELTA)`；`_CREATION_REFLECTION_DELTA=0.2`（决策可推翻，用户定值）。这是「反思 → 想表达的冲动」——创造欲与读书/自由探索结束时按 07-desire 定义的 `_CREATION_ACTIVITY_PRESSURE_DELTA` 加压并列为创造欲的两类压力源；活动结束事件来源契约见 09-activity
- **`add_long_term` 归 07（ripple）**：`DesireFacade.add_long_term(desire: LongTermDesire) -> None` 做容量检查 + 精确/语义去重后委托 `store.insert_long_term`。反思走 DesireFacade 而非 DesireStore
- **`reflect(correlation_id: str | None = None) -> ReflectionOutcome`**：公开调用先准备再以本地事务提交，并在提交后 announce `REFLECTION_DONE`；`REFLECTION` durable consumer 先检查 effect marker，在事务外完成 prepare，再在同一事务内写 effect marker、慢变量和 `REFLECTION_DONE`，提交后唤醒投递。解析失败或提交失败都会抛出，delivery 进入 retry；不再返回 `None` 表示失败
- **`apply_event` 是统一事件入口**：`bus.subscribe(OBSERVATION_STATE/DESIRE_SATISFIED/ACTIVITY_END/REFLECTION, facade.apply_event)`（组合根绑定）。`apply_event` 对 4 类事件都做「衰减+偏移」，另按类型分派 `ACTIVITY_END→精力`、`REFLECTION→反思`
- **`EMOTION_UPDATE` 发布**：每次 `apply_event` 末尾发布（content `{valence, arousal, emotion}`，`emotion` 是 8 档 `.value` 字符串，经 `resolve_emotion` 求得），供前端 SSE；`correlation_id = 触发事件.correlation_id`
- **`get_state` 依赖注入（决策：已与用户确认）**：构造注入 `ActivityFacade` + `DesireFacade`，`get_state` 调 `get_current()` / `get_pending()` 组装快照。只读、无环——`ActivityFacade.select_activity(desires, state)` 以参数收 `CurrentState`、`DesireFacade` 不反向调 inner_life，故 inner_life → {activity, desire} 不构成环
- **inner_life 无配置段**：情感衰减/精力恢复等用模块级常量（可推翻）；`InnerLifeFacade` 构造收 `config: Config` 仅用于把 `config.desire` 传给 `Reflection`（长期欲望容量）

## 测试要点

- [ ] 单元测试 `tests/test_inner_life/`（`pytest-asyncio`；`db = await connect(":memory:")`；`store = InnerLifeStore(db)`；`reflection = Reflection(store, fake_memory, fake_desire, fake_llm, fake_evaluator, config.desire)`；`facade = InnerLifeFacade(store, fake_activity, fake_desire, fake_memory, bus, fake_llm, fake_evaluator, config)`；fake `LlmClient.complete` 按 `output_type == "reflection"` 返回 fixture JSON 并记录调用、fake `Evaluator.evaluate` 记录调用；fake `ActivityFacade.get_current` / `DesireFacade.get_pending`/`get_all`/`add_long_term` 返回预设；`EventBus` 用真实例 + recording handler，`run()` 作 task 驱动——同 05/09/11 模式）：
  - [ ] **emotion 纯函数**（`test_inner_life_emotion.py`，无 DB）：
    - [ ] `clamp_valence` / `clamp_arousal`：越界夹回 `[-1,1]` / `[0,1]`
    - [ ] `decay_emotion`：`elapsed=0` → 不变；`rate=0` → 不变；`elapsed=1/rate` → 衰减到 0；负 valence 也同乘 f（不反向）
    - [ ] `apply_offset`：加偏移后 clamp；正偏移超上限夹 1、负超下限夹 -1（valence）
    - [ ] `event_offset`：`DESIRE_SATISFIED` → `(0.2, 0.1)`；未知/未登记事件 → `(0.0, 0.0)`
    - [ ] `vad_to_category` 6 档穷尽：`(0.9,0.8)`→happy、`(0.9,0.2)`→shy、`(-0.9,0.8)`→angry、`(-0.9,0.4)`→worried、`(-0.9,0.2)`→sad、`(0.0,0.2)`→neutral；边界（`valence=0.2` 含等号）
    - [ ] `resolve_emotion`：`energy_state=DRAINED` → sleepy（压过一切）；`energy_state=ENERGETIC` + `current_activity=IDLE_REFLECTION` → thinking；`energy_state=OKAY` + `current_activity=READING` → base；`current_activity=None` → base
    - [ ] `energy_to_state`：`100`→energetic、`79`→okay、`59`→tired、`39`→exhausted、`19`→drained（五档边界）
- [ ] **store**（`test_inner_life_store.py`）：
    - [ ] `get_personality` 空表 → `None`；`upsert_personality` 后 `get` 返回五维全等；再 `upsert` 改一维（ON CONFLICT 更新，不重复建行）
    - [ ] `get_values` / `upsert_values` 同上（四维）
    - [ ] `get_aesthetic` / `upsert_aesthetic`：四轴往返、同一 `id='self'` 覆盖
    - [ ] `get_energy` / `upsert_energy`：`value` + `state` 往返（`EnergyState` 枚举）；空表 → `None`
    - [ ] `get_narrative` / `upsert_narrative`：`story`/`self_view`/`becoming` JSON 往返（`self_view` 是 `dict[str,str]`）、`identity` 往返、`updated_at` 往返
  - [ ] **reflection 纯函数**（`test_inner_life_reflection.py`）：
    - [ ] `_drift_dim`：`delta=None` → 不变；`delta=+0.3` → `base+0.3`；`delta=+2` → clamp 到 `+0.5`；`base=9.8, delta=+0.5` → clamp 到 10.0；`base=1.2, delta=-0.5` → clamp 到 1.0
    - [ ] `drift_personality` / `drift_values`：只改 delta 里出现的维、其余维不变；结果 clamp 到 `[1,10]`
    - [ ] `drift_aesthetic`：复用同一漂移和 clamp 规则；缺键不变
    - [ ] `_build_reflection_prompt`：含近期记忆摘要、当前性格/三观数值、叙事身份、长期欲望名；空输入 → 含「（无）」
    - [ ] `_parse_reflection`：合法 JSON → 各字段；缺 `story`/`becoming` → `ValueError`；`self_view` 值非 str → `ValueError`；漂移值非数值 → `ValueError`；漂移 key 不在允许维度集（如 `openess` 拼错）→ `ValueError`（不静默停格）；`long_term_desires` 非数组 → `ValueError`；空 `long_term_desires`/`personality_delta`（缺省/`null`）→ 默认 `[]`/`{}`；`self_view`/`personality_delta`/`long_term_desires` 是 `[]`/`""` 等错类型 → `ValueError`（不静默吞）；单个坏候选 → best-effort 跳过（log），其余合法候选保留、不中断整次回写
    - [ ] `_validate_candidate`：`type` 非法 → `ValueError`；缺 `name` → `ValueError`；`subtopics` 非字符串数组 → `ValueError`
    - [ ] `_to_long_term`：`type` 转 `DesireType`、`strength == _LONG_TERM_INIT_STRENGTH`、`progress == 0.0`
  - [ ] **reflection.run**：
    - [ ] fake LLM 返回完整 JSON → 1 次 LLM 调用（`output_type="reflection"`、`correlation_id` 传入值透传；`run(None)` 时自生成非空）、`evaluator.evaluate` 被调 1 次（收到该 `LLMOutput`）；性格/三观按 delta 漂移回写、叙事 story/becoming 各 +1、self_view 合并；`add_long_term` 被调 `len(候选)` 次；创造欲加压 `pressure_creation(0.2)` 被调 1 次
    - [ ] `long_term_desires` 候选数超过 `long_term_capacity - 现有数` → 只新增到容量上限（不超）
    - [ ] 单行表未 seed（`get_personality` 返回 None）→ `RuntimeError`
    - [ ] story 真新增 → `run` 返回 `ReflectionOutcome(story_is_new=True)`；story 与已有片段重复 → `story_is_new=False`（返回值结构化，非 `str | None`）
  - [ ] **facade**（`test_inner_life_facade.py`，先 `upsert_personality`/`upsert_values`/`upsert_aesthetic`/`upsert_energy`/`upsert_narrative` seed 五张单行表——`apply_event` 末尾 `_publish_emotion` 读 energy、`get_state` 读慢变量，未 seed 会 fail-fast）：
    - [ ] `apply_event(DESIRE_SATISFIED)`：valence/arousal 上升（+0.2/+0.1 后 clamp）；发布 `EMOTION_UPDATE`（content 含 `valence`/`arousal`/`emotion` 字符串、`source is INTERNAL`、`correlation_id == 触发事件.correlation_id`）
    - [ ] `apply_event(ACTIVITY_END)`：content 带 `energy_delta=-25` → `energy` 下降 + `energy_state` 重算 + `upsert_energy` 被调；无 `energy_delta` 键 → 不崩（缺省 0）
    - [ ] 未 seed energy → `apply_event(ACTIVITY_END)` 与 `apply_event(DESIRE_SATISFIED)` 均抛 `RuntimeError`（写路径 `_apply_energy`、读路径 `_publish_emotion` 都 fail-fast，不静默兜底默认值）
    - [ ] `apply_event(REFLECTION)`：先在事务外完成 `Reflection.prepare` 的 LLM 调用，再在事务内应用 `ReflectionPlan`；`correlation_id == 触发事件.correlation_id`；情感偏移也生效（-0.1 arousal）
    - [ ] **惰性结算**：monkeypatch `time.time` 使两次 `apply_event` 间隔 1 天 → 第二次时情感先被衰减；无事件时调用 `get_state` 也结算情感和精力，精力回写 store 且不发布 `EMOTION_UPDATE`
    - [ ] `get_state`：注入 fake `ActivityFacade.get_current`（返回 activity，`current_activity` = `.type`）与 fake `DesireFacade.get_pending`（返回 list）→ `CurrentState` 各字段正确，含 `aesthetic`；未 seed → `RuntimeError`
    - [ ] `get_narrative`：store 有 → 返回；空 → `RuntimeError`
    - [ ] `reflect` 委托：`facade.reflect()` → reflection 的 LLM 被调 1 次，且 LLM 调用发生在本地事务外
    - [ ] `facade.reflect()` 成功 → 在同一事务内提交慢变量与 `REFLECTION_DONE`（content `{story, story_is_new}`、correlation 透传）；解析失败抛 `ValueError`
- [ ] 集成测试：无（LLM 全 mock、DB 用 `:memory:`；ActivityFacade 向前引用用 fake，真实编排归 09-activity 与 04-module-bus-system）
- [ ] E2E 测试：无

## 完成定义

- [ ] `ruff check` 零报错
- [ ] `pyright` 零报错
- [ ] `pytest` 全绿
- [ ] `test-inventory.md` 已更新
- [ ] **联动已同步**：07-desire 的长期欲望入口、04-module-bus-system 的五张单行表、06-memory-system 的 `count_new`、11-expression 的审美 prompt、12-reading-system 的 `kind='reading'` 记忆均按当前源码接线
- [ ] 组合根：`InnerLifeStore(db)` → `InnerLifeFacade(store, activity_facade, desire_facade, memory_facade, bus, llm, evaluator, config)`；启动时 seed 五张单行表（personality 8/8/2/6/7、values 8/6/9/5、aesthetic、energy=100/energetic、self_narrative 初始 identity）；订阅 `OBSERVATION_STATE`/`DESIRE_SATISFIED`/`ACTIVITY_END`/`REFLECTION` 到 `facade.apply_event`
- [ ] 09-activity 的 `activity_end` content 契约（`energy_delta`）与本 spec §技术方案一致；11-expression 拼 prompt 用 `InnerLifeFacade.get_state()`
