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

阅读系统负责用户陪读书库：EPUB 导入、段落读取、用户/Nyx 双位置进度、阅读冲动、
Nyx 陪读事件、用户笔记、Nyx 批注和章末/整本记忆整合。`material` 活动书库是
Nyx 自己读的另一套数据，不由本 spec 替代。

## 数据模型

### 书籍与段落

`Book` 字段为 `id/title/author/filename/content_hash/total_paragraphs/created_at/
updated_at`。`Paragraph` 字段为 `id/book_id/index/text/is_chapter_start`。

- `index` 从 1 起，按书连续。
- `is_chapter_start` 表示段落由 `h1`/`h2` 开始，用于边界检测。
- `content_hash` 是分段正文以换行连接后的 SHA-256，唯一索引防重复书。

### 进度

`ReadingProgress` 字段为 `book_id/user_position/nyx_position/reading_speed/read_count/
updated_at/revision`。`BookListItem` 字段为
`id/title/author/filename/total_paragraphs/user_position/last_read_at`。

- 落库位置范围为 `[1, total_paragraphs]`；书架中无进度行时
  `user_position=0` 只是未读哨兵。
- `reading_speed` 范围为 10-200。
- `read_count` 只由整本读完原子操作递增。
- `revision` 是每书单调递增的条件写版本；无进度行的默认版本是 0。

### 用户笔记与批注

`UserNote` 字段为 `id/book_id/paragraph_id/content/selected_text/created_at/
updated_at/annotations`；`Annotation` 字段为
`id/user_note_id/content/created_at`。

- 书或段删除时用户笔记的外键置空，批注随用户笔记级联删除。
- `content` 和 `selected_text` 均最多 4000 字符，正文不能为空。

## 内容导入契约

- `segment_html(html: str) -> list[Segment]` 是同步纯函数。
- `parse_epub(data: bytes) -> EpubResult` 是同步解析函数；Facade 必须用线程卸载。
- `ReadingStore.insert_book_with_paragraphs(...) -> tuple[Book, bool]` 在一个事务内
  写入书和全部段落；重复 hash 返回已有书和 `False`。
- `ReadingFacade.import_book(filename: str, data: bytes) -> Book`：
  空正文抛 `ValueError`，重复抛 `DuplicateBookError`。

分段规则是块级标签成段、标题与紧邻 `p` 合并、连续 `li` 合并、短段合并、超过
3000 字符按句号拆分；无结构标签的 fallback 也必须经过长段拆分。

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

ReadingFacade.get_progress(book_id: str) -> ReadingProgress
ReadingFacade.save_progress(
    book_id: str,
    user_position: int,
    nyx_position: int,
    reading_speed: int,
    expected_revision: int,
) -> ReadingProgress
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
进度，再以最新 revision 重试，不能直接以旧快照覆盖。

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
- 分派在后台执行，但必须由 Facade 追踪，并提供：

```python
ReadingFacade.quiesce() -> None
ReadingFacade.drain(timeout: float = 10.0) -> bool
```

- 每书后台陪读最多 2 个并发任务；同一书同一段落在途时不得重复派发。
- 原文 prompt 截断为 6000 字符并标明材料边界。
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

## 笔记与整合契约

- `add_user_note`、`list_user_notes`、`update_user_note`、`delete_user_note`、
  `show_to_nyx` 均为异步 Facade 方法。
- `list_user_notes` 先确认书存在，不存在抛 `BookNotFoundError`，一次批量查询批注。
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
- 整合流程为：snapshot buffer → LLM JSON `{content, summary}` → evaluator →
  `remember_reading` →（重读时）成功受理 `REFLECTION` → 删除已消费 snapshot。
  任何一步失败都保留 snapshot；LLM 期间新增的 buffer 条目不得被删除。
- `REFLECTION` 只在整合开始前的 `read_count >= 1` 时发布；首读不发布。
- `GET /api/notes/{book_id}`、用户笔记 CRUD、`show-to-nyx` 和
  `check-chapter-boundary` 的错误映射以 `nyx/api/routes.py` 当前实现为准：
  书/笔记不存在 404，输入边界 422，LLM 批注失败返回 200 `null`。

## 生命周期

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

## 测试与完成定义

- 纯函数测试覆盖分段、特征、驱动、冷却。
- 集成测试覆盖导入原子性、revision 冲突、位置边界、重复整本完成、整合失败保留
  buffer、下游事件失败不清理 buffer 和后台任务追踪。
- API 测试覆盖 2xx/404/409/422/500 语义；前端测试覆盖 revision 写队列和缓存缺失
  时刷新书架。
- `ruff check`、`pyright`、后端 `pytest`、前端 `npm test` 和 `npm run build` 应通过。
