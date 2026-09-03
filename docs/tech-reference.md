# Nyx Agent 技术参考

> 落地细节层：DDL、Facade 签名、API/SSE 契约、LangGraph 图、包结构、配置。（枚举/实体类型见 specs/01-types.md）
> `CLAUDE.md`「写 spec」模板里"涉及的 Facade / 数据变更 / API 端点 / 新增文件"直接抄这里，不现编。
> 约定：主键/ID 用 **uuid4 字符串**，时间戳用 **epoch 秒（浮点）**。

## 1. 枚举（16 个 StrEnum）

> 枚举成员与实现以 `nyx/enums.py` 为准（spec 01-types 只给契约），此处不再重复。§3 起的 DDL / API / Facade 签名直接引用这些枚举。

约定速记：
- 统一 `enum.StrEnum`：成员 `UPPER_SNAKE`、值 = `成员名.lower()` 的 snake_case。
- `EventType` 为初始集，可扩展。
- `EmotionCategory` 8 档选择优先级：**困倦 > 思考 > 情绪**；`vad_to_category` 只落前 6 个情绪，`sleepy`/`thinking` 由精力/认知态覆盖（语义见 design/design.md §4.2，实现见 12-inner-life）。

---

## 2. 实体 dataclass（22 个 + 3 个 TypedDict）

> dataclass 字段与实现以 `nyx/types.py` 为准（spec 01-types 只给契约；固定键字段用 TypedDict、异构载荷用 `dict[str, Any]`）。此处不再重复。

---

## 3. DB DDL（SQLite）

> DDL 与迁移以 `nyx/db.py` 源文件为准（spec 04-db 只给契约；19 张业务表 + 6 个显式索引 + 版本化迁移 + `connect()`），此处不再重复。
> 约定速记：复杂字段（story / becoming / subtopics / progress / aspect / goal / linked_values / self_view / content / embedding）存 JSON 字符串；枚举列存 `.value` 字符串；可空性严格对应 01-types 的 Optional（`X | None` ⟺ DDL 可空）；19 张业务表 + `schema_version` 迁移簿记表 = 共 20 张。迁移版图：v1/2/3/6 基础表（4/5 死号）、v7-v10 陪读（books/paragraphs/progress/user_notes/annotations）、v11-v12 审美维度（aesthetic 表 + `memory.first_created_at`）、v13 eval 记账（`eval_log` 表，15-eval）。

---

## 4. API + SSE 契约

### REST endpoints

| Method | Path | 请求 | 响应 |
|---|---|---|---|
| GET | `/api/state` | — | `CurrentState` JSON |
| POST | `/api/chat` | `{message: str}` | `{event_id: str}`（回复走 SSE） |
| POST | `/api/observe` | `{presence: str, window_title: str}` | `{event_id: str}`（观察状态入口，前端 Tauri 判定后上报） |
| GET | `/api/memories?tag=&type=` | query 过滤 | `Memory[]` |
| GET | `/api/memories/search?q=` | query 语义检索 | `Memory[]`（三层检索：关键词/向量/联想） |
| GET | `/api/desires` | — | `{values, short_term[], long_term[]}` |
| GET | `/api/activity` | — | `{current, schedule[]}` |
| GET | `/api/activity/results` | — | `Activity[]`（跨天历史产出，倒序） |
| GET | `/api/events/log?limit=&event_type=&correlation_id=` | — | `Event[]` |
| GET | `/api/narrative` | — | `SelfNarrative` |
| POST | `/api/export` | `{format: json|md}` | 记忆导出文件 |
| POST | `/api/upload` | `multipart/form-data`（`file`） | `{filename, path}`（落盘后 `register_material` 只注册书库，不立即读书） |
| GET | `/api/materials` | — | `{materials: Material[]}`（书库进度） |
| POST | `/api/books` | `multipart/form-data`（`file`） | `Book`（EPUB 导入：解析→去重→落库；重复 409 / 非 .epub·超限·空正文 400 / 解析失败 500） |
| GET | `/api/books` | — | `BookListItem[]`（书架：已读按 last_read_at DESC 排前、未读按 created_at DESC 排后） |
| GET | `/api/books/{book_id}/paragraphs?from=&to=` | query 范围（`from>=1`、`to>=from`） | `Paragraph[]`（`index` 升序；书不存在 404 / 越界 422） |
| GET | `/api/progress/{book_id}` | — | `ReadingProgress`（书不存在 404；无记录返回默认进度） |
| PUT | `/api/progress/{book_id}` | `{user_position, nyx_position, reading_speed}` | `{ok: true}`（书不存在 404） |
| POST | `/api/impulse/evaluate` | `{book_id, paragraph_index, last_paragraph_index}` | `{triggered: [ReadingBehavior 值]}`（回翻/缺段返回 `[]`；触发分派走 SSE） |
| GET | `/api/notes/{book_id}` | — | `UserNote[]`（含每条的 `annotations` 派生列表） |
| POST | `/api/notes/user` | `{book_id, paragraph_id?, content, selected_text?}` | 201 `UserNote` |
| PUT | `/api/notes/user/{note_id}` | `{content}` | `UserNote`（`NoteNotFoundError` 404） |
| DELETE | `/api/notes/user/{note_id}` | — | 204（`NoteNotFoundError` 404） |
| POST | `/api/notes/{user_note_id}/show-to-nyx` | — | `Annotation`（`NoteNotFoundError` 404） |
| POST | `/api/notes/check-chapter-boundary` | `{book_id, nyx_position>=1}` | `{is_boundary: bool, book_finished: bool}`（`BookNotFoundError` 404） |
| GET | `/api/eval/recent?limit=` | query 条数（默认 5） | `EvalRecord[]`（最近 N 条 LLM 调用记账，倒序） |
| GET | `/api/eval/total_tokens` | — | `EvalStats`（按 `call_id` 去重累计 `total_tokens`/`prompt_tokens`/`completion_tokens`） |
> REST 端点分两类：
> - **读方法薄封装**（无额外业务逻辑）：`/api/state` → `InnerLifeFacade.get_state()`；`/api/memories` → `MemoryFacade.list_memories(tag, type)`；`/api/memories/search` → `MemoryFacade.search(q)`；`/api/desires` → `DesireFacade.get_all()`；`/api/activity` → `ActivityFacade.get_current()` + `get_schedule()`；`/api/activity/results` → `ActivityFacade.get_results()`；`/api/events/log` → `EventBus.list_events(limit, event_type, correlation_id)`；`/api/narrative` → `InnerLifeFacade.get_narrative()`；`/api/export` → `MemoryFacade.export(fmt)`；`/api/materials` → `ActivityFacade.list_materials()`> - **外部输入入口**：`/api/chat`、`/api/observe` 不调 Facade 读方法，而是组合根构造事件 `publish` 后返回 `{event_id}`——`/api/chat` → publish `USER_MESSAGE`（bus 按 ROUTING 路由到 interrupt + `ExpressionFacade.reply()`）；`/api/observe` → publish `OBSERVATION_STATE`（bus 路由到 `InnerLifeFacade.apply_event()` + `DesireFacade.add_value()`）；`/api/upload` → 落盘后 `ActivityFacade.register_material()` 只注册书库（不发事件、不立即读书），返回 `{filename, path}`。回复/后续产出走 SSE。

### SSE（`GET /api/events`）

每个事件一条，`event:` 用 EventType 成员值（小写字符串，如 `speak` / `activity_end`），`data:` 为 JSON。

`data` 统一 = **`event.content` 展开 + `event_id` + `correlation_id`**：

```
data = {"event_id": event.id, "correlation_id": event.correlation_id, **event.content}
```

> 前端拿到该事件的完整 `content`（键结构由各生产方 spec 定义）再加两个溯源字段。`event.content` 的键从不与 `event_id`/`correlation_id` 冲突（生产方不产这两个键），展开安全。示例：`speak` → `{event_id, correlation_id, content}`；`activity_end` → `{event_id, correlation_id, activity_id, desire_id, goal_met, energy_delta, result}`，其中 `result` 形状随 `ActivityType` 变（读书→`{book, note, read_chars, total_chars}`、创作→`{title, content, path}`、探索→`{summary, core_discovery, knowledge, findings}`、发呆→`{summary}`、休息→`{}`、观察→`{presence, window_title, summary}`，见 14-activity）。

> **陪读冲动事件（21）**：`reading_mutter`（`{content, book_id, paragraph_index}`）、`reading_question`（`{content, subtype, book_id, paragraph_index, selected_text}`）、`reading_association`（`{memory_id, snippet, book_id, paragraph_index}`）——`POST /api/impulse/evaluate` 后台分派广播，仅广播前端、无内部消费者（空路由）。

> **SSE vs REST 切分**：SSE 实时推送**全部事件**（前端按 `event` 类型增量更新面板）；REST 只做**初始快照 + 历史查询 + 导出**（`GET /api/state` 初始快照，`/api/memories` `/api/events/log` 列表查询）。

---

## 5. Facade 清单 + 方法签名

> 三层：Facade → 子系统 → 内部类。此处列 Facade 公开方法（方法名 + 入参/出参类型），子系统见 §7 包结构，内部类不在此列。
>
> **发布约定**：Facade 内部自己 `publish` 它产生的事件，返回值只可能是 `None` 或**数据对象**（`Memory` / `Activity` / `CurrentState` / `list` / `bool`），**绝不返回 `Event` 让调用方发布**；产出统一由 EventBus 广播到 SSE。

### ExpressionFacade

```python
async def reply(msg: str, correlation_id: str) -> None          # 完整回复流程，内部发布 speak/ask/think
async def initiate_chat(desire: ShortTermDesire, state: CurrentState) -> bool  # 内部发布 initiate_chat；发话 True/无话 False（18-api 据此维护 last_chat_at）
async def mutter(state: CurrentState, correlation_id: str) -> None  # 内部发布 mutter（无则不发）；correlation_id 接 MUTTER_CHECK tick
async def check_timeouts(now: float) -> None                    # tick 心跳收尾：问句超时记「没答」记忆、搭话超时 expire 回灌
def record_proactive_turn(text: str) -> None                     # 把 Nyx 主动产出（读书提问/联想）追加进 _history，供 reply() 回溯引用（同步，纯内存 append）
```

### MemoryFacade

```python
async def create_scene_memory(reply_context: dict[str, str]) -> Memory    # 场景化记忆（慢通道）
async def search(query: str) -> list[Memory]                    # 内部跑三层+去重合并
async def record_recall(memory_id: str) -> None                 # 记录"想起"
async def list_memories(tag: str | None = None, type: MemoryType | None = None, limit: int | None = None) -> list[Memory]  # 仪表盘过滤 + 可选截断
async def count_new(tag: str, since: float) -> int             # 计数「首次创建晚于 since」的 tag 记忆（first_created_at 锚点，轻量不物化 embedding）
async def export(fmt: str) -> str                              # 记忆导出（json|md）
async def remember_knowledge(items: list[dict[str, str]], correlation_id: str) -> None  # 读书知识点入长期记忆（tag='knowledge'，无 LLM）
async def remember_reading(content: str, summary: str, correlation_id: str) -> None  # 章节/整本读书记忆入长期（tag='reading'，无 LLM）
```

### ActivityFacade

```python
async def on_tick(tick_type: TickType) -> None                  # 排期/评估触发
async def on_desire_generated(event: Event) -> None             # DESIRE_GENERATED 触发消费欲望
def select_activity(desires: list[ShortTermDesire], state: CurrentState) -> Activity | None  # desires 来自 DesireFacade.get_pending()；无欲望/全互动欲返回 None（纯决策，同步）
async def complete_activity(activity: Activity) -> None         # 内部发布 activity_end（满足信号等）
async def interrupt(activity_id: str, by: EventType) -> None    # 抢占即暂停：可续活动（读书/创作/探索）置 PAUSED、其余置 ABANDONED；同日程块内 _maybe_start_activity 恢复同一记录
async def get_current() -> Activity | None                      # 当前活动（running），供快照/仪表盘
async def get_schedule() -> list[Activity]                      # 今日日程块（供 /api/activity）
async def get_results(limit: int = 100) -> list[Activity]       # 跨天历史产出（供 /api/activity/results）
async def list_materials() -> list[Material]                    # 书库全量（含已读进度，供 /api/materials 资料面板）
async def register_material(path: str, filename: str, total_chars: int) -> None  # 注册读物进书库（只登记不立即读；读书由欲望驱动选书）
```

### ReadingFacade

```python
# 构造：ReadingFacade(store, inner_life, desire, memory, llm, evaluator, bus, canon, expression)  # 9 依赖注入
async def import_book(filename: str, data: bytes) -> Book       # 解析 EPUB → 去重 → 落库 books+paragraphs → 返回 Book；正文重复抛 DuplicateBookError、空正文抛 ValueError
async def list_books() -> list[BookListItem]                    # 书架列表（直通 store，已读排前）
async def list_paragraphs(book_id: str, from_idx: int, to_idx: int) -> list[Paragraph]  # 段落范围（index 升序）；书不存在抛 BookNotFoundError、to_idx 越界抛 ValueError
async def get_progress(book_id: str) -> ReadingProgress         # 进度；书不存在抛 BookNotFoundError、无进度行返回默认（1,1,50,0,0.0）
async def save_progress(book_id: str, user_position: int, nyx_position: int, reading_speed: int) -> ReadingProgress  # 写进度 UPSERT（不碰 read_count）；书不存在抛 BookNotFoundError
async def evaluate_paragraph(book_id: str, paragraph_index: int, last_paragraph_index: int) -> list[ReadingBehavior]  # 翻页冲动：现算 6 驱动→复合→阈值+冷却→后台分派；回翻/缺段返回 []
async def add_user_note(book_id: str, paragraph_id: str | None, content: str, selected_text: str | None) -> UserNote  # 用户笔记落库（book_id/paragraph_id 可 None 自由记）
async def list_user_notes(book_id: str) -> list[UserNote]   # 笔记列表（每条附批注 annotations 派生列表）
async def update_user_note(note_id: str, content: str) -> UserNote  # 改笔记内容；不存在抛 NoteNotFoundError
async def delete_user_note(note_id: str) -> None             # 删笔记（批注 CASCADE）；不存在抛 NoteNotFoundError
async def show_to_nyx(note_id: str) -> Annotation            # 用户「给 Nyx 看」→ LLM 生成批注（无 json_mode）；不存在抛 NoteNotFoundError
async def record_nyx_output(book_id: str, paragraph_index: int, content: str, source: str) -> None  # mutter/question 入内存 buffer（source∈{mutter,question}）
async def check_chapter_boundary(book_id: str, nyx_position: int) -> BoundaryResult  # 章节边界判定；命中边界后台 `_integrate_buffer`；书不存在抛 BookNotFoundError
```

### DesireFacade

```python
async def add_value(source: Event) -> None                      # 事件入口：OBSERVATION_STATE 互动欲加压 + ACTIVITY_END 满足回写
async def evaluate(energy: float = 100.0) -> list[ShortTermDesire]  # 峰值→LLM 生成；energy < ENERGY_REST_THRESHOLD 时先给休息欲加压
async def get_pending() -> list[ShortTermDesire]                # 读待消费队列（pending/active，非破坏，供排期/拼 prompt）
async def get_all() -> DesireState                              # 全量快照（values+短期+长期，供 /api/desires）
async def satisfy(desire_id: str, goal_met: bool) -> None       # 达成/未达成回写；达成时发布 desire_satisfied（inner_life 消费更新情感）
async def expire(desire_id: str) -> None                        # 淘汰→值回增
async def mark_active(desire_id: str) -> None                   # PENDING → ACTIVE：活动开始消费（仅 PENDING 可转，幂等 no-op）
async def mark_suppressed(desire_id: str) -> None               # ACTIVE → SUPPRESSED：中断/异常停车（仅 ACTIVE 可转，幂等 no-op）
async def add_long_term(desire: LongTermDesire) -> None         # 探索/反思共用长期欲望入口（去重 + 容量检查，满不新增）
async def pressure_creation(delta: float) -> None               # 创造欲加压（反思成功 +0.2；读书/自由探索结束 +0.15）
```

### InnerLifeFacade

```python
async def apply_event(event: Event) -> None                     # 情感/精力更新
async def reflect(correlation_id: str | None = None) -> None    # 协调器：内部调 MemoryFacade/DesireFacade，改性格/三观/审美/长期欲望/自我叙事；correlation_id 来自触发事件（缺省自生成）
async def get_state() -> CurrentState                           # 只读快照（含 personality/values/aesthetic/energy 等慢变量）
async def get_narrative() -> SelfNarrative                      # 自我叙事（供 /api/narrative）
```

### EventBus（基础设施，非 Facade）

```python
def __init__(self, db: Database) -> None                         # 组合根注入；db.lock 串行化共享连接的并发访问（05-event）
async def publish(event: Event) -> None                          # 入队即返回；持久化/分发/广播由 run() 完成
def subscribe(event_type: EventType, handler: Callable[[Event], Awaitable[None]]) -> None
def add_sse_sink(sink: asyncio.Queue[Event]) -> None             # SSE 客户端注册（05-event 补）
def remove_sse_sink(sink: asyncio.Queue[Event]) -> None          # SSE 客户端注销（05-event 补）
async def run() -> None                                         # 主循环
async def list_events(limit: int = 100, event_type: EventType | None = None, correlation_id: str | None = None) -> list[Event]  # event_log 历史查询（供 /api/events/log）
```

### ToolRegistry（基础设施）

```python
def register(tool: Tool) -> None
async def call(name: str, args: dict) -> Any
def schema() -> list[dict]                                      # 给 LLM 的 tool schema
```

### Evaluator（基础设施）

```python
def __init__(self, embed: EmbedFn | None = None, store: EvalStore | None = None) -> None  # embed 供 OOC embedding 档；store 供记账（15-eval），None 不落库
async def evaluate(output: LLMOutput) -> None                    # 算 ooc_score +（voice 且有 embed）ooc_embed_score；store 非 None 时写一条 eval_log（best-effort，落库失败不重抛）
```

> `EvalStore`（`nyx/eval/store.py`）提供 `insert` / `list_recent(limit=5)` / `total_tokens()`；`total_tokens` 按 `call_id` 分组去重（think/speak 共享一次 complete 的 token 只计一次）。

---

## 6. LangGraph 图定义

### 6.1 回复流程（`expression/pipeline.py`）

**State**：

```python
class ReplyState(TypedDict):
    message: str
    mode: ContextMode            # fast | slow
    context: list[Message]       # 回溯上下文（不含当前消息）
    memories: list[Memory]       # 检索到的记忆
    state: CurrentState          # 当前状态快照
    narrative: SelfNarrative | None   # 慢通道 assemble 填充，快通道恒 None
    think: list[str]             # 累积：每轮 think 追加
    speak: list[str]             # 累积：每轮 speak 追加
    ask: str | None
    round: int                   # 连续无 ask 的 respond 轮数（≤ slow_max_rounds）
    correlation_id: str          # 本次 reply 溯源
    tool_outputs: list[str]      # use_tools 查到的工具结果（慢通道专属）
```

**Nodes**：`classify_channel` → `assemble_context` → `use_tools` → `respond` → `should_ask` → `generate_scene_memory` → `record_message`

> `respond` 一轮 think+speak 用一次 LLM 生成（`json_mode`，`_parse_reply` 解析 JSON 产出 think/speak，再分开发 THINK/SPEAK/ASK 事件）。

**Edges**：

```
start → classify_channel
  ├ fast → respond → record_message → end            # 跳过记忆检索+场景化记忆；respond 一轮 think+speak 一次生成
  └ slow → assemble_context → use_tools → respond → should_ask
             ├ 非问句：round+1，publish SPEAK（每轮交付；think 每轮 publish THINK）
             │     round < slow_max_rounds → 回到 respond（连续无 ask 最多 slow_max_rounds 轮）
             │     round ≥ slow_max_rounds → generate_scene_memory → record_message → end
             └ 问句：publish ASK → generate_scene_memory → record_message → end
                     # MVP 问句即回合结束（用户回应作为下一条 USER_MESSAGE 触发新 reply，round 自然重算）
```

---

## 7. 包结构（`nyx/`）

```
nyx/
  __init__.py
  main.py                 # uvicorn 服务入口：FastAPI 端点 + 组合根 + tick 循环
  config.py               # 配置加载（§8）
  enums.py                # §1 所有枚举
  types.py                # §2 实体 dataclass
  db.py                   # SQLite 连接 + 19 表 DDL + 版本化迁移 + Database(conn, lock)（04-db）
  events/
    bus.py                # EventBus
    routing.py            # ROUTING 表
    event.py              # internal_event 内部事件构造 + SECONDS_PER_DAY/PER_HOUR 时间常量
  memory/
    facade.py             # MemoryFacade
    store.py              # SQLite 存取
    graph.py              # networkx 联想图
    retrieval.py          # 三层检索
  reading/
    __init__.py
    segmenter.py           # segment_html（HTML 正文→阅读段落，纯函数）
    epub.py                # parse_epub（EPUB 字节→元数据+段落+content_hash）
    store.py               # ReadingStore（books/paragraphs/reading_progress/user_notes/annotations 存取：进度/书架/分页/increment_read_count/笔记批注 CRUD）
    impulse.py             # 阅读冲动引擎纯函数（特征提取 / 驱动现算 / 复合加权 / 阈值+冷却，21）
    facade.py              # ReadingFacade（import_book / list_books / list_paragraphs / get_progress / save_progress / evaluate_paragraph / add_user_note / list_user_notes / update_user_note / delete_user_note / show_to_nyx / record_nyx_output / check_chapter_boundary）
  expression/
    facade.py             # ExpressionFacade
    pipeline.py           # 回复流程（LangGraph）
    prompt.py             # prompt 拼装（状态段含审美四轴）
    classifier.py         # 快慢通道判定
    mutter.py             # 碎碎念模板
  activity/
    facade.py             # ActivityFacade
    store.py              # ActivityStore（activity 表单表 CRUD）
    material_store.py     # MaterialStore（书库分块进度 + 读书笔记片段）
    scheduler.py          # 日程块排期
    exploration.py        # 联网探索（线性）
    observe.py            # 观察用户
    screen.py             # 屏幕视觉（截屏+ScreenObserver，opt-in）
  desire/
    facade.py             # DesireFacade
    store.py              # SQLite 存取（short_term_desire / desire_value / long_term_desire 三表）
    value.py              # 值机制（纯函数）
    lifecycle.py          # 全周期
  inner_life/
    facade.py             # InnerLifeFacade
    emotion.py            # valence/arousal/标签
    reflection.py         # 反思（drift_aesthetic + 阅读量缩放）
    store.py              # InnerLifeStore（personality/value_system/aesthetic/energy/self_narrative 五张单行表）
  tools/
    registry.py           # ToolRegistry
    local_search.py       # 本地搜索
    web_search.py         # 联网搜索（opt-in）
    file_io.py            # 读写本地文件
  eval/
    evaluator.py          # Evaluator
    store.py              # EvalStore（eval_log 记账，15-eval）
    rules.py              # 规则层（纯函数）
  llm/
    client.py             # LangChain 统一客户端
    vision.py             # VisionClient（多模态视觉，OpenAI 兼容）
```

> 对应测试目录见 `CLAUDE.md`「测试规范」。

---

## 8. 配置 surface（`config.yaml`）

```yaml
llm:
  provider: deepseek          # deepseek | openai | ollama；其它 OpenAI 兼容服务配 base_url
  model: deepseek-chat
  api_key_env: DEEPSEEK_API_KEY
  # base_url: http://localhost:11434/v1   # 可选：覆盖/自定义 endpoint
  timeout: 60.0               # 单次 LLM 请求超时（秒）
  max_retries: 2              # 请求失败重试次数

embedding:
  model: all-MiniLM-L6-v2     # 本地 sentence-transformers

memory:
  short_term_capacity: 100    # 容量上限
  promote_threshold: 3        # 想起 3 次升级长期
  freshness_decay: 0.01       # 新鲜度衰减率

desire:
  peak_threshold: 0.9         # 值达峰阈值
  retry_limit: 3              # 未达成重试上限
  long_term_capacity: 5       # 长期欲望上限
  value_decay: 0.05           # 值缓慢衰减率

activity:
  grid_minutes: 60            # 每小时一块
  energy_delta:
    reading: -20
    creation: -25
    free_exploration: -30
    observe_user: -10
    idle_reflection: 10
    rest: 30

expression:
  slow_threshold: 0.5         # 快慢通道阈值：classifier 加权 5 因子→归一化得分(0-1)→比此值
  max_context_len: 20         # 回溯上下文上限
  slow_max_rounds: 3          # 慢通道最多轮数
  ask_timeout: 600.0          # ask 后等用户回答超时（秒）
  chat_ignore_timeout: 1800.0 # 搭话被忽略判定超时（秒）
  context_time_gap: 3600.0    # 回溯上下文相邻消息隔超此值即停（秒）

exploration:
  web_enabled: false          # 联网搜索 opt-in
  rate_limit_hours: 1         # 自由探索频率上限

vision:
  enabled: false              # 屏幕视觉 opt-in（手动开启）
  provider: ollama            # 视觉模型 provider→base_url 映射（复用 llm 映射）
  model: llava                # 本地视觉模型 tag（Ollama）
  api_key_env: DEEPSEEK_API_KEY  # 存环境变量名，key 本体由 03-llm 的 VisionClient 读（Ollama 免 key 占位）
  # base_url: http://localhost:11434/v1   # 可选：覆盖视觉 endpoint
  interval_seconds: 60        # 抓屏周期（秒）
```

---

## 待补（TODO）

- 三观/性格/长期欲望初始值：见 `docs/canon.md`，数值待用户校准
