# 阅读系统

> 本 spec 是阅读系统唯一完整契约，合并原内容导入、进度、冲动和笔记四份 spec。
> 它只定义对外签名、数据语义和可验证决策；实现细节以 `nyx/` 源码为准。

## 元信息

- **前置依赖**：`01-types`、`03-llm`、`04-module-bus-system`、`06-memory-system`、
  `07-desire`、`08-inner-life`、`10-eval`、`11-expression`
- **实现文件**：
  `nyx/types.py`、`nyx/enums.py`、`nyx/db.py`、
  `nyx/reading/segmenter.py`、`nyx/reading/epub.py`、
  `nyx/reading/store.py`、`nyx/reading/facade.py`、
  `nyx/reading/companions.py`、`nyx/reading/impulse.py`、
  `nyx/reading/integration.py`、`nyx/expression/facade.py`、
  `nyx/api/routes.py`、`nyx/app_context.py`、`nyx/main.py`、
  `frontend/src/api/client.ts`、`frontend/src/stores/readerStore.ts`、
  `frontend/src/types/api.ts`

## 范围

阅读系统负责唯一书籍库：EPUB 导入、结构化段落读取、用户/Nyx 双位置进度、阅读冲动、
Nyx 陪读事件、用户笔记、段内划线、书签、Nyx 批注和章末/整本记忆整合。活动系统通过本
Facade 的窄入口枚举和读取 EPUB，不复制书籍或段落，也不维护第二套通用文件书库。

## 数据模型

### 书籍与段落

`Book` 字段为 `id/title/author/filename/content_hash/total_paragraphs/created_at/
updated_at`。`Paragraph` 字段为
`id/book_id/index/text/is_chapter_start/blocks/marks`。

- `ParagraphBlock` 字段为 `kind/start/end/level`；`kind` 只允许
  `paragraph/heading/blockquote/list_item/pre`，仅 `heading` 使用 `level=1..6`。
- `TextMark` 字段为 `start/end/bold/italic`。`blocks`、`marks` 与用户划线的 offset
  都是 `Paragraph.text` 的 UTF-16 code unit 闭开区间（与 JavaScript `slice` 一致）；
  后端按同一规则生成和校验，前端不得从 HTML 重新推导。
- `text` 仍是记忆、LLM、哈希、字数与选区校验的唯一纯文本来源；`blocks/marks`
  只负责受控渲染。旧数据的两数组为空时按普通正文回退。

- `index` 从 1 起，按书连续。
- `is_chapter_start` 表示段落由 `h1`/`h2` 开始，用于边界检测。
- `content_hash` 是分段正文以换行连接后的 SHA-256，唯一索引防重复书。
- `books.memory_state` 是内部 JSON checkpoint，不进入 `Book` 或书架 API：
  `cursor={paragraph_index, char_offset}` 指向下一未沉淀字符，`profile` 保存滚动摘要、最多 5
  个主题和内容类别，`pending` 保存一次已经完成提取但尚未确认写入的结果。旧库默认 `{}`，
  读取时补成 cursor 起点、空摘要/主题和 `unknown`。

### 进度

`ReadingProgress` 字段为 `book_id/user_position/nyx_position/reading_speed/read_count/
updated_at/revision`。`BookListItem` 字段为
`id/title/author/filename/total_paragraphs/user_position/last_read_at`。

- 落库位置范围为 `[1, total_paragraphs]`；书架中无进度行时
  `user_position=0` 只是未读哨兵。
- `reading_speed` 范围为 10-200。
- `read_count` 只由整本读完原子操作递增。
- `revision` 是每书单调递增的条件写版本；无进度行的默认版本是 0。

### 用户笔记、划线、书签与批注

`UserNote` 字段为 `id/book_id/paragraph_id/paragraph_index/content/selected_text/
selection_start/selection_end/created_at/updated_at/annotations`；`paragraph_index`
由段落关联派生、不单独落库。`Annotation` 字段为
`id/user_note_id/content/created_at`。`Bookmark` 字段为
`id/book_id/paragraph_id/paragraph_index/preview/created_at`，其中后两项段落信息由查询派生。

- 书或段删除时用户笔记的外键置空，批注随用户笔记级联删除。
- `content` 和 `selected_text` 均最多 4000 字符。普通笔记的 `content` 非空；纯划线允许
  `content=""`，但必须同时提供有效的 `paragraph_id/selected_text/selection_start/
  selection_end`，并满足 `paragraph.text[start:end] == selected_text`。
- 旧笔记可以只有 `selected_text` 而无 offset；它继续显示为笔记引用，但不在正文绘制划线。
- 划线只支持单个 `Paragraph`；完全相同的纯划线幂等返回已有记录。重叠划线各自保留，
  前端只合并视觉区间，删除一条后其余记录仍生效。
- 书签与笔记分表；同一本书同一段最多一个书签，书或段删除时级联删除。前端书签加载
  的成功或失败回包只有在请求书仍是当前 `bookId` 时才能写入 store，旧书晚到回包丢弃。

## 内容导入契约

- `segment_html(html: str) -> list[Segment]` 是同步纯函数。
- `parse_epub(data: bytes) -> EpubResult` 是同步解析函数；Facade 必须用线程卸载。
- `ReadingStore.insert_book_with_paragraphs(...) -> tuple[Book, bool]` 在一个事务内
  写入书和全部段落；重复 hash 返回已有书和 `False`。
- `ReadingFacade.import_book(filename: str, data: bytes) -> Book`：
  空正文抛 `ValueError`，重复抛 `DuplicateBookError`。

分段规则仍是块级标签成段、标题与紧邻 `p` 合并、连续 `li` 合并、短段合并、超过
3000 字符按句号拆分；无结构标签的 fallback 也必须经过长段拆分。分段同时保存当前
已识别块标签的语义，以及 `strong/b`、`em/i` 两类行内标记；文本必须先 HTML 解码，
不得保存原始 HTML、脚本、样式、属性或 EPUB CSS。拆分与合并后的 offset 必须仍能精确
切回 `Paragraph.text`。`p` 等嵌套块位于 `li`、`blockquote` 或 `pre` 时，保持原有文档
顺序与分段边界，同时继承最近语义容器的块类型，不能降级成普通 paragraph。

## 进度契约

### Store 与 Facade

```python
ReadingStore.get_progress(book_id: str) -> ReadingProgress | None
ReadingStore.upsert_progress(
    book_id: str,
    user_position: int,
    nyx_position: int,
    reading_speed: int,
    expected_revision: int,
) -> ReadingProgress
ReadingStore.increment_read_count(
    book_id: str, nyx_position: int
) -> ReadingProgress
ReadingStore.reset_completion_marker(
    book_id: str, nyx_position: int
) -> bool
ReadingStore.advance_nyx_position(
    book_id: str, nyx_position: int
) -> ReadingProgress

ReadingFacade.get_progress(book_id: str) -> ReadingProgress
ReadingFacade.save_progress(
    book_id: str,
    user_position: int,
    nyx_position: int,
    reading_speed: int,
    expected_revision: int,
) -> ReadingProgress
ReadingFacade.read_for_activity(
    book_id: str,
    target_paragraph: int | None,
    correlation_id: str,
) -> dict[str, Any]
```

- `save_progress` 先确认书存在并校验两处位置不越界。
- 首次 `expected_revision=0` 时插入 revision=1；之后只允许匹配当前 revision
  的条件更新，成功后 revision 加 1。
- 版本不匹配抛 `ProgressConflictError`，REST 映射 409。
- REST `PUT /api/progress/{book_id}` 请求体必须包含
  `{user_position, nyx_position, reading_speed, expected_revision}`，成功返回完整
  `ReadingProgress`。
- `GET /api/progress/{book_id}` 对不存在书返回 404，对无进度行返回默认值。
- `GET /api/books/{book_id}/paragraphs?from=&to=` 对非法范围返回 422，不截断越界。

前端必须保存服务端 revision；同书进度写入串行化。冲突或写失败时先读服务端最新
进度，再以最新 revision 重试。除显式“重读”外，重试的 `nyx_position` 必须取本地与
服务器较大值并更新本地状态，不能以旧快照覆盖活动后台已经推进的 Nyx 位置。

### 进度同步事件

后端在 `save_progress` 或后台 `advance_nyx_position` 成功持久化完整进度后，发布
`READING_PROGRESS`。事件 payload 固定包含
`book_id/user_position/nyx_position/reading_speed/read_count/revision`，并通过既有
`GET /api/events` SSE 广播；它是状态快照通知，不表示阅读器已经消费或完成其它事件。

- 只有持久化成功后才构造并广播事件；广播失败只记录日志，不回滚已提交的
  `reading_progress`。
- 前端只在当前书匹配、`revision` 严格新于本地且 `nyx_position` 不回退时应用快照；
  异书、重复/旧 revision 和 Nyx 位置回退都丢弃。应用后按服务端位置启动或停止追赶，
  不覆盖本地尚未提交的 `user_position`。
- 事件可能与用户写入、活动读取和 SSE 重连乱序到达；revision 是唯一的进度顺序依据，
  不以事件到达时间或浏览器接收时间作决定。

活动系统读取 EPUB 时只调用 `read_for_activity`：明确任务读到指定目标；探索欲匹配书籍
时读取约一个 6000 字符段落范围。该入口复用原文沉淀，成功后才单调推进 Nyx 位置，
保留用户位置、阅读速度和读完次数，不触发陪读冲动、主动提问、联想或用户翻页语义。

### 活动系统读取 EPUB

- 明确委派任务传 `target_paragraph`；普通探索欲匹配 EPUB 时传 `None`，从当前 Nyx 位置向前选择约一个 6000 字符块的段落范围。
- `read_for_activity` 复用 `ReadingIntegration.sediment(..., flush=True)`，成功后才调用 `advance_nyx_position`。推进取 `max(current, requested)`，保留服务端当前 `user_position/reading_speed/read_count` 并递增 revision。
- 目标已不晚于持久化 `nyx_position` 时幂等返回完成，不回退、不重复沉淀；只有阅读器显式“重读”允许 Nyx 位置回退。
- 目标到书末时沿用既有整本完成幂等语义，首次完成递增 `read_count`；活动读取不写陪读 buffer，因此不生成冲动、提问、联想或主观碎碎念整合。
- `book_id` 不存在与目标段越界分别由调用方映射为 404/422；排队期间书被删除或段落范围变化时，活动任务按 09-activity 进入可见失败态。

阅读器普通上一段/下一段继续调用 `syncPosition` 并按前进段落评估冲动。点击历史划线或
书签调用独立的 `jumpToPosition`：正式更新并持久化 `user_position`、按需重拉窗口并恢复
Nyx 追赶，但无论向前或向后都不调用 `evaluateImpulse`，避免把定位跨过的内容伪装成逐段阅读。

## 阅读冲动契约

- `extract`、`build_drives`、`compute_composite`、`check_triggers` 是同步纯函数。
- `ReadingFacade.evaluate_paragraph(book_id, paragraph_index, last_paragraph_index)`
  对回翻、缺书、缺段返回空列表；只对前进段落计算并返回触发行为。
- 四类提问行为（`question_knowledge`、`question_personal`、
  `question_reflective`、`quote_question`）先分别通过各自阈值，再只选择复合分最高的
  一个；同一段最多触发一个提问。若无提问候选，则不产生提问行为。
- 四类提问共享 `ReadingFacade` 进程级的 180 秒单调钟冷却；冷却在恰好 180 秒时
  结束，服务重启后清零。`associate` 保持独立的 60 秒冷却，`mutter` 保持独立的
  30 秒冷却，因此联想或碎碎念仍可与一个提问并存。
- 提问冷却只在 LLM 输出通过合法性校验并成功提交 `READING_QUESTION` 后开始；后台
  任务进行中只占用一个待定提问名额，空输出、非法问句、提交失败或任务取消都会释放
  名额且不消耗 180 秒冷却。
- 分派在后台执行，但必须由 Facade 追踪，并提供：

```python
ReadingFacade.quiesce() -> None
ReadingFacade.drain(timeout: float = 10.0) -> bool
```

- 每书后台陪读最多 2 个并发任务；同一书同一段落在途时不得重复派发。
- 原文 prompt 截断为 6000 字符并标明材料边界。
- 每次前进到新段落都在现有阅读后台任务集合中请求原文沉淀；同一本书由
  `ReadingIntegration` 的 per-book lock 串行处理，不改变冲动返回值或阻塞翻页接口。
- 非空 mutter 才能发布 `READING_MUTTER`；合法问题才可发布
  `READING_QUESTION`。
- `READING_ASSOCIATION` 每次最多 3 条，使用 `MemoryFacade.search`，不写 Nyx buffer。

## 提问原子性

`ExpressionFacade.commit_reading_question(text, source_id, correlation_id,
event_content) -> str` 必须在一个本地事务内提交：

1. `expression_interaction_attempt` waiting 行；
2. `ASK` durable event；
3. `READING_QUESTION` durable event。

任一步失败，事务整体回滚；成功提交后才允许唤醒消费者和将内容计入阅读整合
buffer。读取系统在兼容未提供该方法的 fake 时可以退回旧路径，但正式组合根必须提供
该方法。

用户回答后不需要在 attempt 中复制原文：表达层使用 `source_id` 回读 book/paragraph，并
调用 `ReadingFacade.build_reply_context()` 取得 06-memory 的来源内 Top 5 事实正文与当前
书籍画像；Top 5 使用 `Memory.content`，不以主题型 `summary` 代替事实。

## 原文沉淀与书籍画像

- 原文按最多 6000 字符组成块；cursor 可以停在段落中间，因此单个超长段落会拆成多个
  有界提取块，但 `paragraphs.text` 保持原样，回答该段提问时仍提供完整原段落。
- 累积满 6000 字符立即沉淀；章末和整本末尾 flush 小于 6000 的余量。块可跨多个短段落，
  不为“每段”额外调用 LLM。
- 每块使用上一 profile + 本块原文生成新 profile 和最多 5 条知识；profile 为
  `{summary, themes, content_category}`，类别固定为
  `fiction/nonfiction/essay/poetry/drama/reference/unknown`。
- 顺序固定为：生成结果 -> 持久化 `pending` -> `remember_knowledge` -> 推进 cursor、采纳
  profile、清空 pending。提取/解析失败不推进 cursor；记忆写入后崩溃则重放 pending，
  由统一记忆去重吸收，不增加第二套幂等表。
- fiction 事实归于作品/人物/虚构世界；essay 归于作者主张；nonfiction/reference 可成为
  领域知识；unknown 保守注明来源。书中第一人称不得归于用户/Nyx。
- 重读已经越过 cursor 的段落不重复沉淀；同名书按 `book_id` 来源 topic 隔离。重复章末
  调用由已有 integration task 去重和 per-book lock 共同保证不会并发处理同一 pending。

真实 bad case 的处理边界：

| 情况 | 处理 |
|---|---|
| 提取完成后进程退出 | pending 已落库，恢复不再调用提取 LLM |
| knowledge 已写、cursor 未推进 | 重放 pending；现有记忆去重不新增重复行 |
| 章末/书末不足 6000 字符 | 边界 flush |
| 单段超过 6000 字符 | cursor 的 `char_offset` 分块；回复仍读取完整段落 |
| 同名书 | source topic 基于 `book_id`，不用标题作隔离键 |
| 旧库无画像 | 空摘要/主题 + `unknown` |
| 提取 JSON 非法 | 记录后台错误并保留 cursor，后续前进/边界调用重试 |
| 重复边界调用 | 不创建第二个并行整合任务；锁内重读最新 checkpoint |
| 活动目标已经读过 | 幂等返回，不重复沉淀、不回退、不重复增加读完次数 |
| 活动后台与阅读器并发保存 | 普通进度冲突重读服务端快照，Nyx 位置取本地/服务端最大值；显式重读除外 |

### 最小实施计划

1. 在 `books` 行使用 JSON `memory_state`，不新增来源表、配置项或服务层；旧 `material`
   数据源由 09-activity 的退役迁移删除。
2. 复用 `MemoryFacade` 增加 6000 字符原文整理、稳定 source topic、同源去重与来源内检索；
   knowledge 仍走现有统一持久化和事实抽取尾段。
3. EPUB 翻页后台累计满块沉淀，章末/书末 flush；网页/本地探索复用现有结果 cursor 保存
   单条 `source_pending`。
4. 读书提问 attempt 只保留现有 `source_id`；用户回答时回读同书 Top 5 事实正文、完整触发
   段落和书籍画像，并同时注入 FAST/SLOW，不复制第二份原文到 attempt。
5. 用同名书、全局高分干扰、短回复、超长段落、边界余量和两个崩溃窗口做回归；同步
   memory/activity/expression/reading 契约与事实摘要。

## 笔记、划线、书签与整合契约

- `add_user_note`、`list_user_notes`、`update_user_note`、`delete_user_note`、
  `show_to_nyx` 均为异步 Facade 方法。
- `list_bookmarks`、`add_bookmark`、`delete_bookmark` 均为异步 Facade 方法；新增前校验
  书与段落归属，重复新增幂等返回已有书签。
- `list_user_notes` 先确认书存在，不存在抛 `BookNotFoundError`，一次批量查询批注。
- “快速查询”只搜索当前书已加载的有效划线，匹配 `selected_text`（大小写不敏感）；
  不调用 LLM、不搜索 Nyx 记忆、不新增服务端全文搜索端点。点击结果使用上文
  `jumpToPosition` 语义。
- `show_to_nyx` 使用统一人格 prompt；LLM 失败或空输出返回 `None`，不插入批注。
- Nyx 输出仅支持 `source="mutter"` 或 `"question"`，写入每书最多 100 条的进程内
  buffer，重启后清空。
- `check_chapter_boundary(book_id, nyx_position) -> BoundaryResult`：
  - 下一段是章节首时返回 `CHAPTER_END`；
  - 位置达到或超过总段数时返回 `BOOK_FINISHED`；
  - 其他情况返回 `NONE`。
- `BOOK_FINISHED` 先原子递增 `read_count` 并持久化 `nyx_position=total`，再后台整合；
  空 buffer 或整合失败不撤销读完计数。
- 同一本书同一时间只有一个整合任务。重复边界请求不得重复计数或并发调用 LLM；
  失败后只要 buffer 仍在，后续边界调用可以重试。
- 边界任务先 flush 原文沉淀，再运行既有 Nyx 主观输出整合；两者分别保留 checkpoint，
  主观 buffer 为空不影响原文 flush。
- 整合流程为：snapshot buffer → LLM JSON `{content, summary}` → evaluator →
  `remember_reading` →（重读时）成功受理 `REFLECTION` → 删除已消费 snapshot。
  任何一步失败都保留 snapshot；LLM 期间新增的 buffer 条目不得被删除。
- `REFLECTION` 只在整合开始前的 `read_count >= 1` 时发布；首读不发布。重读事件使用普通 durable payload `{reason: "reading_revisit", book_id, evidence: summary}`，把本轮整合摘要作为触发证据保留；反思 consumer 仍会从旧记忆整体归纳，不把该摘要当作唯一材料。
- `GET /api/notes/{book_id}`、用户笔记 CRUD、`show-to-nyx` 和
  `check-chapter-boundary` 的错误映射以 `nyx/api/routes.py` 当前实现为准：
  书/笔记不存在 404，输入边界 422，LLM 批注失败返回 200 `null`。

## 生命周期

## 阅读证据对象完整性

阅读暴露证据与知识/主观记忆分开保存，复用 `MemoryFacade` / `MemoryStore`，
不新增服务层。`ReadingEvidence` 是不可变快照，含 id、source_topic、source_name、
content、created_at；每条正文最多 6000 字符。schema 36 新建 `reading_evidence`。

**入口清单**

| 入口 | 产生条件 | 写入位置 |
|---|---|---|
| `ReadingIntegration.sediment` | EPUB 原文块成功沉淀，即使 knowledge 为空 | pending 保留原文证据；证据与 cursor 同事务提交 |
| `ActivityFacade._run_web_task` | 明确的网页阅读任务块成功沉淀 | task pending 保留证据；证据与 checkpoint 同事务提交 |
| pending 恢复 | 新版 pending 带完整证据 | 同一来源、块定位和正文 hash 生成固定 id，重复插入 no-op |

旧 pending 缺原文时不猜测补证据；旧库不追补阅读量。自由探索不写阅读证据。

**消费者清单**

| 消费者 | 发现方式 | 用途 |
|---|---|---|
| `Reflection.prepare` | `MemoryFacade.pending_reading_evidence(limit=3)`，按 created_at/id 升序 | 独立审美 prompt；不以知识点数量或主观输出次数计量 |
| `Reflection.apply` | plan 的 evidence ids | 在反思事务内复核存在且未消费，并标记消费 |

**状态迁移表**

| 当前状态 | 条件 | 下一状态 | 副作用 / 失败落点 |
|---|---|---|---|
| 不存在 | 原文沉淀与证据 checkpoint 提交成功 | 未消费 | 写固定 id；失败回滚证据与 checkpoint |
| 未消费 | 独立审美结果有效且反思提交成功 | 已消费 | consumed_at 与慢变量/effect 同事务写入 |
| 未消费 | LLM/解析/引用失败 | 未消费 | 审美不变，其他反思可提交 |
| 任意 | EPUB 来源书删除 | 不存在 | book_id 外键 CASCADE；在途反思提交失败并重试 |

**Bad case 表**

| 情况 | 处理 |
|---|---|
| 空 | 空原文不记录；无证据不调用审美 LLM；空 knowledge 不影响证据 |
| 失败 | 证据存储错误传播；审美调用失败保留证据 |
| 部分完成 | 只提交成功的块；只消费实际入 prompt 的最多三块，后续块留到下轮 |
| 乱序 | 晚到块不按叙事时间戳跳过；按未消费行发现；提交复核防并发消费 |
| 重放 | 固定 id 幂等；重读相同来源/范围/正文不重复增加阅读量 |
| 删除 | EPUB 随书删除；网页无公开来源删除入口，可随本地 DB 清理；不引用已删除证据 |

主观整合按实际发送的条目/字符范围消费，12000 字符外的尾部保留；
等待期间 buffer 满额淘汰后新增的条目不因旧 snapshot 长度被删。
`remember_reading` 使用基于 book_id 的稳定来源 topic 与真实书名；旧未知来源不回填。
QUOTE_QUESTION 必须严格两行，第二行是实际发送的截断原文中的非空连续子串。
存储/状态读取错误不能降级为批注 `null`；仅 LLM/eval 失败或空批注返回 `null`，
等待期间笔记删除返回 404。

- 服务器停止接收新请求后，必须先调用 `ReadingFacade.quiesce()`，再取消 tick/观察等
  循环，随后调用 `ReadingFacade.drain()`，最后关闭 EventBus 和共享 DB。
- drain 超时会取消剩余阅读后台任务；未完成的 buffer 保留在进程内并由后续边界或重启
  恢复策略处理，不能在任务未完成时提前关闭共享数据库。

## API 清单

| 方法 | 路径 | 语义 |
|---|---|---|
| POST | `/api/books` | 导入 EPUB |
| GET | `/api/books` | 书架 |
| GET | `/api/books/{book_id}/paragraphs` | 读取段落窗口 |
| GET | `/api/progress/{book_id}` | 读取进度 |
| PUT | `/api/progress/{book_id}` | revision 条件写进度 |
| POST | `/api/impulse/evaluate` | 评估段落冲动 |
| GET | `/api/notes/{book_id}` | 笔记和批注 |
| POST | `/api/notes/user` | 新增用户笔记 |
| PUT | `/api/notes/user/{note_id}` | 修改用户笔记 |
| DELETE | `/api/notes/user/{note_id}` | 删除用户笔记 |
| POST | `/api/notes/{note_id}/show-to-nyx` | 生成批注 |
| POST | `/api/notes/check-chapter-boundary` | 检查章末/整本边界 |
| GET | `/api/bookmarks/{book_id}` | 当前书书签 |
| POST | `/api/bookmarks` | 新增书签（同段幂等） |
| DELETE | `/api/bookmarks/{bookmark_id}` | 删除书签 |

## 测试与完成定义

- 纯函数测试覆盖分段格式与 offset、特征、驱动、冷却。
- 集成测试覆盖导入原子性、revision 冲突、位置边界、重复整本完成、整合失败保留
  buffer、下游事件失败不清理 buffer 和后台任务追踪。
- 原文记忆测试覆盖 6000 字符块、跨段 cursor、超长单段、章/书末 flush、pending 两个
  崩溃窗口、来源内 Top 5、画像滚动与类别归因、短回复三层上下文。
- 活动读取测试覆盖明确目标、约 6000 字符自动目标、只推进 Nyx、已读目标 no-op、整本计数幂等与 revision 并发合并。
- API 测试覆盖 2xx/404/409/422/500 语义；前端测试覆盖 revision 写队列、缓存缺失
  时刷新书架、划线检索、书签切换、切书后旧书签回包丢弃，以及定位保存进度但不补发
  冲动。
- 进度同步测试覆盖 `READING_PROGRESS` 完整字段、持久化成功后广播、广播失败不回滚，
  以及前端异书/旧 revision/重复 revision/Nyx 回退过滤和追赶控制。
- 阅读器使用受控结构渲染标题、正文、加粗、斜体、引用、列表与预格式文本；版心限制
  行宽。富文本、字号或窗口变化后沿用实测真分页。单段高于视口时该页允许局部纵向
  滚动，不能用 `overflow:hidden` 裁掉正文。重叠划线与行内格式按排序端点扫描生成原子
  区间，不得在每个原子区间重新遍历全部划线。
- `ruff check`、`pyright`、后端 `pytest`、前端 `npm test` 和 `npm run build` 应通过。
