# 阅读冲动引擎（段落特征 → 6 驱动「现算」 → 复合触发 → 分派）

> 范围：`nyx/reading/` 模块的**行为层**——用户翻页时对当前段落提取特征、现算 6 维驱动、加权复合、阈值+冷却判定，触发提问/记忆联想；碎碎念（mutter）是独立的「段落精彩度」快通道闸门（`richness_score > 0.5`），**不进复合权重**。只管「段落进、冲动事件出」，不含笔记（22）、审美维度（23，只通过 `state` 间接注入 mutter prompt）。
> spec 只定义契约（签名 + 语义 + 决策），不内联完整代码；代码唯一事实来源是 `nyx/` 源文件。

## 元信息

- **前置依赖**：19-reading-content（`paragraphs` 表）、20-reading-progress（`ReadingFacade.list_paragraphs`）、09-memory-facade（`search`）、11-desire（`get_all`）、12-inner-life（`get_state`）、17-expression（复用 `build_system_prompt`）
- **反向修订 08-events**：08-events 现行 `EventType` 无 reading 事件、`ROUTING` 无对应条目；本 spec 负责**新增** 3 个 `EventType`（`READING_MUTTER`/`READING_QUESTION`/`READING_ASSOCIATION`），且**加 3 条空路由**（`READING_MUTTER`/`READING_QUESTION`/`READING_ASSOCIATION` → `[]`，仅广播前端、无内部消费者，与 `MUTTER`/`SPEAK` 同款「空路由」；穷尽断言 `set(ROUTING) == set(EventType) - {CLOCK_TICK}` 强制所有事件类型都在表里，故空列表键必须存在）。08-events 是「被扩展」的既有 spec，不是前置依赖。
- **反向修订 18-api**：`ReadingFacade` 构造签名由 19 的 `(store)` 扩为 8 参 `(store, inner_life, desire, memory, llm, evaluator, bus, canon)` + `build_app_context` 装配更新（19 已甩给本 spec）+ `POST /api/impulse/evaluate` 端点（`_App.reading` 字段 19 已加）。18-api 是「被扩展」的既有 spec，不是前置依赖。
- **实现文件**：`nyx/enums.py`（新增 `ReadingDrive`/`ReadingBehavior` + 3 个 `EventType`）、`nyx/reading/impulse.py`（纯函数 + 常量）、`nyx/reading/facade.py`（追加 `evaluate_paragraph` + 分派）、`nyx/main.py`（端点 + 组合根装配）
- **无数据变更**：冲动驱动「现算」、冷却时间戳内存态，不新增 `impulse_params`/`impulse_events` 表（见「关键决策」）

## 用户故事

> 作为用户，我翻到精彩的一段时，Nyx 会碎碎念/提个问题/冒出记忆联想；回翻或重读同一段时她不会重复反应，让她像真的在旁边一起读。

## 验收标准

- [ ] `nyx/enums.py` 含 `ReadingDrive`（6 值）与 `ReadingBehavior`（5 值）两个 `StrEnum`
- [ ] `ReadingDrive` = `motivation`/`curiosity`/`boredom`/`aesthetic_sensitivity`/`empathy_bias`/`associative_drive`（照搬 S06 `constants.py DRIVE_NAMES`）
- [ ] `ReadingBehavior` = `question_knowledge`/`question_personal`/`question_reflective`/`quote_question`/`associate`（照搬 S06 `composite_engine.py DEFAULT_COMPOSITE_WEIGHTS` 键，**无 mutter**——mutter 是独立快通道闸门，见「关键决策」）
- [ ] `nyx/enums.py` 的 `EventType` 追加 `READING_MUTTER`/`READING_QUESTION`/`READING_ASSOCIATION`
- [ ] `nyx/reading/impulse.py` 含 `ParagraphFeatures` 数据类（10 字段，见「关键决策」）+ 纯函数 `extract(text: str) -> ParagraphFeatures`（同步、无 IO、无 LLM）
- [ ] `nyx/reading/impulse.py` 含 `build_drives(features: ParagraphFeatures, *, energy: float, agreeableness: float, exploration_value: float, interaction_value: float) -> dict[ReadingDrive, float]`（同步纯函数，6 驱动「现算」）
- [ ] `nyx/reading/impulse.py` 含 `compute_composite(drives: dict[ReadingDrive, float]) -> dict[ReadingBehavior, float]`、`check_triggers(composite: dict[ReadingBehavior, float], cooldowns: dict[ReadingBehavior, float], now: float) -> list[ReadingBehavior]`
- [ ] `ReadingFacade.evaluate_paragraph(book_id: str, paragraph_index: int, last_paragraph_index: int) -> list[ReadingBehavior]`（`async`）
- [ ] 触发分派：mutter（独立闸门）/ 4 提问 / associate 各走 LLM（或记忆检索）→ 经 `bus.publish` 广播对应 `EventType`
- [ ] `POST /api/impulse/evaluate`：`{book_id, paragraph_index, last_paragraph_index}` → 200 `{triggered: [...]}`（复合行为列表，`mutter` 不在其中、经 SSE 单独广播）；`paragraph_index <= last_paragraph_index` → `{triggered: []}`
- [ ] `pyright` strict 零报错

## 技术方案

### 涉及的 Facade / 内部类

- `nyx/reading/impulse.py`（纯函数层，无 IO）：关键词集常量、`ParagraphFeatures` 数据类、`extract`、`associative_density`/`empathy_density`、`build_drives`、`compute_composite`、`check_triggers`、权重/阈值/冷却常量（模块常量，见下）
- `ReadingFacade`（追加行为方法 + 依赖注入）：`evaluate_paragraph` 编排「翻页方向守卫 → 取段 → 取状态 → 现算 → 复合 → 判定 → 记冷却 → 后台分派」，其中翻页方向守卫（`paragraph_index <= last_paragraph_index`）与取段为空（书/段不存在）都**提前返回 `[]`**；`_dispatch`/`_mutter_reading`/`_question_reading` 私有方法做 LLM 与 `bus.publish`

### ReadingFacade 追加注入依赖

（对齐 `ExpressionFacade` 的构造注入风格）：`inner_life`（`get_state`）、`desire`（`get_all`）、`memory`（`search`）、`llm`、`evaluator`、`bus`、`canon`；再加实例态 `_cooldowns: dict[ReadingBehavior, float]`（5 复合行为的上次触发时间戳，空起、无持久化）+ `_mutter_at: float`（mutter 上次触发时间戳，空起、无持久化）。

**构造签名**（19 的 `ReadingFacade(store)` 扩为 8 参，本 spec 负责扩签）：`ReadingFacade.__init__(self, store: ReadingStore, inner_life: InnerLifeFacade, desire: DesireFacade, memory: MemoryFacade, llm: LlmClient, evaluator: Evaluator, bus: EventBus, canon: str) -> None`。

### 组合根装配（本 spec 反向扩展 18-api）

- `build_app_context` 里 `reading = ReadingFacade(store, inner_life, desire, memory, llm, evaluator, bus, canon)`；装配位置在 `inner_life`/`desire`/`memory`/`llm`/`evaluator`/`bus`/`canon` **全部构造之后**（较 19 的「`inner_life` 之后」更靠后——从 0 依赖扩为 7 个依赖，`inner_life` 的 `_get_state` 环解已在 18-api 先于本装配完成），与 `ExpressionFacade` 同级（都吃 `llm`/`evaluator`/`bus`/`canon`）。`_App` 加 `reading: ReadingFacade` 字段（19 已定）。
- `build_app` 里注册 `POST /api/impulse/evaluate` 端点闭包调 `app.reading.evaluate_paragraph(...)`（薄封装，错误映射见「API 端点」）。

### ParagraphFeatures 字段（照搬 S06 `feature_extractor.py`，砍到 10 字段）

| 字段 | 类型 | 含义 |
|---|---|---|
| `exclamation_ratio` | `float` | 感叹号密度（`！`/`!` 计数 / 字符数） |
| `quote_ratio` | `float` | 引号/对话标记密度（`"`/`「` 计数 / 字符数） |
| `dash_ratio` | `float` | 破折号/省略号密度（`——`/`…`/`...` 计数 / 字符数） |
| `negative_emo` | `float` | 负面情绪关键词密度 |
| `positive_emo` | `float` | 正面情绪关键词密度 |
| `philosophical` | `float` | 哲学关键词密度 |
| `sensory` | `float` | 感官关键词密度 |
| `character_mention` | `float` | 角色/人称密度（`他`/`她`/`说道` 等） |
| `uniqueness` | `float` | 字频倒数均值（0-1，出现 1 次的 CJK 字占比） |
| `richness_score` | `float` | 0-1 综合「丰富度」加权和 |

> 前 9 个是「原始密度/比率」（~0.01 量级，除 `uniqueness` 本已 0-1）；`richness_score` 是综合分。关键词表（负面/正面/哲学/感官/角色）硬编码在 `impulse.py`，照搬 S06。
> **砍掉 S06 的 4 个未消费字段**（反冗余）：`char_count`/`sentence_count`/`avg_sentence_length`/`question_ratio`——无任何 drive/richness 公式引用它们（`question_ratio` 语义上「满是问号的段落该喂提问」，但 S06 本身也没接线、V1 不擅自加新接线，一并砍掉；日后要接线再回补）。

### 6 驱动「现算」（`build_drives`，全部归一到 [0,1]）

| 驱动 | 现算公式 |
|---|---|
| `motivation` 动力 | `energy / 100`（精力，0-100） |
| `curiosity` 好奇 | `exploration_value`（探索欲压力值，已 0-1） |
| `boredom` 无聊 | `interaction_value`（互动欲压力值，已 0-1） |
| `associative_drive` 记忆联想 | `0.4 + 0.6 × associative_density(features)` |
| `aesthetic_sensitivity` 审美敏感 | `features.richness_score` |
| `empathy_bias` 共鸣倾向 | `0.6 × (agreeableness/10) + 0.4 × empathy_density(features)` |

- `associative_density` = `0.5×philosophical + 0.33×sensory + 0.17×(negative_emo+positive_emo)`，权重重归一自 S06 `feature_weights.associative_drive`（0.3/0.2/0.1 → 0.5/0.33/0.17）。
- `empathy_density` = `0.6×(negative_emo+positive_emo) + 0.4×character_mention`（情感密度 + 角色提及密度，0.6/0.4）。
- 二者经 `_saturate(x, cap)` 线性饱和到 [0,1]（`cap` = 「明显存在」密度阈值，模块常量）。`cap` 绝对值实现时定，但**必须使「典型富段落」的密度饱和到 ~0.5-1.0 区间**——否则 `0.55`/`0.60` 复合阈值与 mutter 闸门 `0.5` 永不可达（S06 原始密度 ~0.01 量级，cap 过大则全部压扁）。单元测试按「富段落 > 平段落」相对序断言（不锁 cap 绝对值），集成测试用「明显富段落」fixture 保证跨阈。`energy`/`agreeableness` 取自 `inner_life.get_state()` 的 `CurrentState`；`exploration_value`/`interaction_value` 取自 `desire.get_all()` 的 `DesireState.values`（按 `DesireType.EXPLORATION`/`INTERACTION` 查，缺省 0.0）。

### 复合权重 / 阈值 / 冷却（模块常量，照搬 S06 `composite_engine.py`，**5 行为、无 mutter**）

| 行为 | 复合权重（驱动 → 权重） | 阈值 | 冷却 |
|---|---|---|---|
| `question_knowledge` | curiosity 0.50 / associative_drive 0.30 / motivation 0.20 | 0.55 | 120s |
| `question_personal` | empathy_bias 0.50 / motivation 0.30 / curiosity 0.20 | 0.60 | 180s |
| `question_reflective` | empathy_bias 0.40 / aesthetic_sensitivity 0.40 / curiosity 0.20 | 0.55 | 150s |
| `quote_question` | curiosity 0.40 / empathy_bias 0.40 / associative_drive 0.20 | 0.65 | 180s |
| `associate` | associative_drive 0.60 / curiosity 0.20 / empathy_bias 0.20 | 0.50 | 60s |

- **mutter 独立闸门**：`richness_score > MUTTER_RICHNESS_THRESHOLD（0.5）` 且 `now - _mutter_at >= MUTTER_COOLDOWN_SEC（30）`。`aesthetic_sensitivity = richness_score`，故等价 S06 代码的 `aesthetic_sensitivity > 0.5`。30s 冷却来自 S06 过时 `spec.md §6.6`（S06 代码里 mutter 频率由 S12 `should_mutter` 管，V1 无 S12，用 30s 冷却作替代），作模块常量 `MUTTER_COOLDOWN_SEC`。
- **`boredom` 无消费方**：忠实照搬 S06——5 个复合行为权重均未引用 `boredom`，故 `boredom` 现算但不进任何复合公式。保留它是为了对齐设计文档 §5.2 的 6 驱动枚举。
- **`richness_score` 校准修正**：S06 的 richness 用原始密度（~0.01）加权，阈值 0.5 永不可达（latent bug）。V1 改为对输入特征先 `_saturate`（`uniqueness` 本已 0-1 不再饱和）再按 S06 同款权重（0.20 哲学 / 0.20 情感 / 0.20 停顿 / 0.15 感叹 / 0.15 独特性 / 0.10 引号）求和、`clamp` [0,1]，使 `aesthetic_sensitivity` 真正跨 [0,1] 可用——mutter 闸门 `richness_score > 0.5` 依赖此校准才可触发。

### 关键决策

- **驱动「现算」、不累积不衰减**（用户已确认）：参考项目 S06 的「事件影响表 + 每秒衰减 + `impulse_params` 落库」整套**不搬**。6 驱动每次翻页由「V1 状态快照 + 段落特征」现算，无状态残留、无新表。冷却时间戳是唯一内存态（per 进程，重启清零，可接受）。
- **mutter 独立快通道、不进复合**（用户已确认）：`ReadingBehavior` 只有 5 个复合行为（4 提问 + `associate`）。mutter 由「段落精彩度」单闸门 `richness_score > 0.5` 触发（等价 S06 代码的 `aesthetic_sensitivity > 0.5`），独立于 `compute_composite`/`check_triggers`，有自己的冷却时间戳 `_mutter_at`（30s）。忠实 S06 代码（其 `composite_engine.py` 无 mutter 键）；语义也更自然（「翻到精彩段 → 碎碎念」）。
- **提问 4 子型 = 4 个复合行为，1:1 映射**：`question_knowledge`/`question_personal`/`question_reflective`/`quote_question` 各自是独立复合行为（不是单一 `question` 行为的子分支）。行为名 = `reading_question` 事件的 `subtype` = 提问 LLM prompt 的 `output_type`。`quote_question` 额外产出 `selected_text`（段落内的引用划线，产出机制见「关键决策」的拆行条）。
- **`associate` 查记忆、上限 3**：触发 `associate` → `memory.search(段落文本)`（V1 的 `search` 只收 `query: str`、无 `limit` 参数，facade 里取前 3 条 `[:3]`，对齐 S06 `retrieve_memories(limit=3)`）→ 每条命中记忆广播一条 `READING_ASSOCIATION`（`snippet` = `summary or content` 截断 ~80 字）。
- **冷却读写同同步块、无需锁**：`evaluate_paragraph` 里 `check_triggers`（读 `_cooldowns`）→ mutter 闸门（读 `_mutter_at`）→ 记冷却（写 `_cooldowns[b] = now` / `_mutter_at = now`）是**连续同步块、无 `await` 隔断**，asyncio 事件循环天然串行，无并发竞态；分派（LLM/memory search）在 `asyncio.create_task` 后台任务里跑，不在读写之间。冷却写由 `evaluate_paragraph` 独家执行（在触发判定后、后台分派前）。
- **分派为 best-effort、不阻塞端点**：mutter/提问的 LLM 与 `associate` 的记忆检索在后台任务里跑，`evaluate_paragraph` 同步返回 `triggered`（复合行为列表）。LLM 空/失败只记日志、不广播、不反噬翻页主流程。每处 `llm.complete` 后调 `evaluator.evaluate(output)`（mutter + 4 提问有 LLM 故 evaluate；`associate` 无 LLM 不 evaluate）。
- **LLM prompt 契约**（复用 17 的 `build_system_prompt`）：mutter/提问均 `build_system_prompt(canon, state)` + 一句 user prompt（「说一句自然口语的碎碎念，一两句」/「基于这段文字问一个知识型/私人/反思型问题」）。`llm.complete(module="reading", output_type=…, correlation_id=book_id)`；mutter 用 `output_type="reading_mutter"`，提问用 `output_type=行为名`（`"question_knowledge"` 等，即 `subtype`）。`state` = `inner_life.get_state()` 的 `CurrentState` 快照（传给 `build_system_prompt`）。
- **`quote_question` 的 `selected_text` 单次 LLM 拆行产出**：其余 4 行为（mutter + 3 提问）只产一个 `content` 单字符串；`quote_question` 的 prompt 要求输出**两行**——第一行一句问题、第二行从段落原文**逐字摘取**的一句引用（不改写）——`llm.complete` 仍返回 `output_type="quote_question"` 的**单字符串**，facade 按**第一个换行**拆（`content, _, quote = raw.partition("\n")`）：`content`=首行 strip、`selected_text`=次行 strip（空 → `None`，回退为不带划线的普通提问）。**不二次 LLM、不做文本回匹配**（逐字由 prompt 约束）。
- **翻页方向守卫**：`paragraph_index <= last_paragraph_index`（重读/回翻）直接返回 `[]`，不触发不广播（mutter 一并抑制）——对齐 S06 FR-006/007，防回翻时重复反应。
- **正文由后端取**：端点只收 `{book_id, paragraph_index, last_paragraph_index}`，正文经 `list_paragraphs(book_id, paragraph_index, paragraph_index)` 取，前端不回传 `paragraph_text`（与 20 的「后端唯一内容来源」一致）。
- **审美维度只经 `state` 注入**：`aesthetic_sensitivity` 驱动用 `richness_score`（段落特征），**不依赖** 23 的审美表；审美维度（23）落在 `CurrentState.aesthetic` 上、由 23 改 `expression/prompt.py` 的 `_state_block` 显式加审美行才进 mutter prompt（`CurrentState` 加字段不自动进 prompt，见 23）。故 21 不硬依赖 23。
- **分派经现有总线**（设计文档 §4）：mutter/question/associate 全部 `bus.publish` → 既有 `GET /api/events` SSE 广播，前端阅读面板按 `EventType` 过滤。`correlation_id` 用 `book_id`（按书归组）。

### API 端点

- `POST /api/impulse/evaluate` → 请求 pydantic 模型 `{book_id: str, paragraph_index: int, last_paragraph_index: int}` → 200 `{triggered: [ReadingBehavior.value, ...]}`；段落/书不存在 → `{triggered: []}`（幂等，不 404）；`paragraph_index <= last_paragraph_index` → `{triggered: []}`；缺键/类型错 422
- 分派产出的 3 个事件（经 `/api/events` SSE）：
  - `READING_MUTTER`：`{content, book_id, paragraph_index}`
  - `READING_QUESTION`：`{content, subtype, book_id, paragraph_index, selected_text|null}`（`subtype` = 4 提问子型之一；`selected_text` 仅 `quote_question` 非空）
  - `READING_ASSOCIATION`：`{memory_id, snippet, book_id, paragraph_index}`（每个命中记忆一条，`snippet` 为 `summary or content` 截断 ~80 字）

## 测试要点

- [ ] 单元测试 `tests/test_reading/test_impulse.py`（纯函数，无 IO）：
  - [ ] `extract`：已知文本（含哲学词/感叹号/对话标记）→ `philosophical>0`、`exclamation_ratio>0`、`richness_score ∈ [0,1]`；富段落 `richness_score` > 平段落
  - [ ] `build_drives`：`energy=100` → `motivation=1.0`；`agreeableness=10` 且无情感段落 → `empathy_bias=0.6`；情感/角色密集段落 → `empathy_bias` 更高；`exploration_value`/`interaction_value` 直通 `curiosity`/`boredom`
  - [ ] `compute_composite`：已知 drives → 复合值 = Σ(驱动 × 权重)（抽查 `question_knowledge`/`associate` 两行为，按上表权重断定期望值）
  - [ ] `check_triggers`：越过阈值且不在冷却 → 触发；冷却期内 → 拒绝；低于阈值 → 不触发（`now` 显式注入，不依赖真实时钟）
- [ ] 集成测试 `tests/test_reading/test_reading_facade.py`（`:memory:` + fake `inner_life`/`desire`/`memory` + fake `bus` + mock `llm`）：
  - [ ] `evaluate_paragraph` 前向翻页 → 返回触发行为，且对应 `READING_*` 事件已 `publish`（fake `bus` 捕获）
  - [ ] `paragraph_index <= last_paragraph_index` → 返回 `[]`、零广播
  - [ ] 段/书不存在（fake store `list_paragraphs` 空）→ 返回 `[]`、零广播（幂等，不 404/422）
  - [ ] 同一行为连续两次（冷却期内）→ 第二次不重复触发
  - [ ] 富段落（`richness_score > 0.5`）→ `READING_MUTTER` 已广播；平段落 → 无 mutter
  - [ ] `associate` 触发 → `memory.search` 被调用、命中记忆各广播一条 `READING_ASSOCIATION`
  - [ ] `quote_question` 触发 → mock LLM 返回「问题\n引用原文」→ `READING_QUESTION` 事件 `content`=「问题」、`selected_text`=「引用原文」；mock LLM 返回单行 → `selected_text`=null
- [ ] 契约测试 `tests/test_api/test_reading_api.py`（fake `ReadingFacade`）：
  - [ ] `POST /api/impulse/evaluate` → 200 `{triggered: [...]}`；缺 `last_paragraph_index` → 422

## 完成定义

- [ ] `ruff check` 零报错
- [ ] `pyright` 零报错
- [ ] `pytest` 全绿
- [ ] `test-inventory.md` 已更新（快照）
- [ ] ripple 同步：tech-ref §1 枚举计数 +2（`ReadingDrive`/`ReadingBehavior`，后者 5 成员）、§5 补 `ReadingFacade`（构造 8 参 `(store, inner_life, desire, memory, llm, evaluator, bus, canon)` + `evaluate_paragraph`）、§7 补 `reading/impulse.py`、§4 REST 表补 `POST /api/impulse/evaluate` + SSE 三事件（`reading_mutter`/`reading_question`/`reading_association`）、18-api 装配反向扩展（`build_app_context` + `_App.reading`）、01-types 枚举计数 +2 + `EventType` 穷尽断言 EXPECTED +3 成员
- [ ] 翻页 → 精彩处 Nyx 碎碎念/提问/联想出现；回翻不重复
