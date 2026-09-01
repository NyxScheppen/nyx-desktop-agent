# 双缓冲笔记（用户笔记 + Nyx buffer + 章末整合 + 展示批注）

> 范围：`nyx/reading/` 模块的**笔记层**——用户笔记（`user_notes` + `annotations` 两表 + CRUD + 「给尼克斯看」批注）+ Nyx 笔记（碎碎念/提问汇入**内存 buffer**，章末/读完整本 LLM 整合后落 `memory` 表 `tag='reading'`）。不新建 `nyx_notes` 表。
> spec 只定义契约（签名 + 语义 + 决策），不内联完整代码；代码唯一事实来源是 `nyx/` 源文件。

## 元信息

- **前置依赖**：19-reading-content（`books`/`paragraphs`）、20-reading-progress（`list_paragraphs` + `get_progress` 的 `read_count` + `increment_read_count`）、21-reading-impulse（注入 `llm`/`memory`/`inner_life`/`evaluator`/`bus`/`canon` + `_mutter_reading`/`_question_reading` 产出点）、09-memory-facade（新增 `remember_reading`）、12-inner-life（`InnerLifeFacade.reflect`）、04-db（`_MIGRATIONS` v10）
- **反向修订 21**：22 在 21 的 `_mutter_reading`/`_question_reading` 里、`llm.complete` 产出 `content` 后（与 `bus.publish` 同一方法内、best-effort）追加调用 `record_nyx_output(book_id, paragraph_index, content, source)`（`source="mutter"`/`"question"`）；`associate`（记忆检索、无 LLM 产出）不调。21 是「被扩展」的既有 spec（同时是前置依赖——22 复用其注入的 `llm`/`memory`/`inner_life`/`evaluator`/`bus`/`canon`）。
- **反向修订 18-api**：本 spec 在 `build_app` 追加 6 个笔记端点闭包（`GET /api/notes/{book_id}` / `POST /api/notes/user` / `PUT /api/notes/user/{id}` / `DELETE /api/notes/user/{id}` / `POST /api/notes/{user_note_id}/show-to-nyx` / `POST /api/notes/check-chapter-boundary`）。18-api 是「被扩展」的既有 spec，不是前置依赖。
- **实现文件**：`nyx/types.py`（新增 `UserNote`/`Annotation`）、`nyx/db.py`（`_MIGRATIONS` 追加 v10）、`nyx/reading/store.py`（笔记/批注 CRUD）、`nyx/reading/facade.py`（笔记方法 + buffer + 整合 + 模块级 `NyxBufferEntry` dataclass）、`nyx/memory/facade.py`（新增 `remember_reading`）、`nyx/enums.py`（新增 `BoundaryResult`）、`nyx/main.py`（端点）

## 用户故事

> 作为用户，我选中一段记笔记、或在面板自由记；点「给尼克斯看」得到她的批注；Nyx 读完一章后后台把她这章的碎碎念/提问整理成章末笔记（落记忆），读完整本再留一条全书记忆。

## 验收标准

- [ ] `nyx/db.py` 的 `_MIGRATIONS` 追加 v10（`user_notes` + `annotations` 两表 DDL，见「数据变更」）
- [ ] `nyx/types.py` 含 `UserNote`：`id/book_id/paragraph_id/content/selected_text/created_at/updated_at`（`id/content/created_at/updated_at` 非 Optional）
- [ ] `nyx/types.py` 含 `Annotation`：`id/user_note_id/content/created_at`（全非 Optional）
- [ ] `MemoryFacade` 新增 `remember_reading(content, summary, correlation_id)`（`tag='reading'`、`LONG_TERM`、无文本生成——不调 `llm.complete`；`_persist_memory` 的 embedding 照常）
- [ ] `ReadingFacade` 追加 `add_user_note` / `list_user_notes(book_id)` / `update_user_note` / `delete_user_note` / `show_to_nyx(note_id) -> Annotation | None`（全 `async`；`add_user_note` 书不存在抛 `BookNotFoundError`、段落不存在/跨书抛 `ValueError`；`show_to_nyx` LLM 失败/空返回 `None`）
- [ ] `ReadingFacade` 追加 `record_nyx_output(book_id, paragraph_index, content, source)`（内存 buffer 追加，`source ∈ {mutter, question}`——associate 无 LLM 产出、不进 buffer；超 `_NYX_BUFFER_MAXLEN` 丢弃最旧条目）
- [ ] `ReadingFacade` 追加 `check_chapter_boundary(book_id, nyx_position) -> BoundaryResult`（章末/整本读完检测 + 后台整合；`BoundaryResult` = `NONE`/`CHAPTER_END`/`BOOK_FINISHED`；`BOOK_FINISHED` 时先同步 `increment_read_count` 再后台整合；同一次读完的重复调用只 `++` 一次、`nyx_position` 回到 `< total` 后下一遍读完再次 `++`）
- [ ] 章末整合 / 读完整本整合均经 LLM 整合 buffer → `remember_reading` 落 `memory`（`tag='reading'`）
- [ ] 重读（`read_count >= 1`）时每次整合额外触发 `inner_life.reflect`；首读（`read_count == 0`）不触发；整本读完时同步 `increment_read_count`
- [ ] 端点：`GET /api/notes/{book_id}`、`POST /api/notes/user`、`PUT /api/notes/user/{id}`、`DELETE /api/notes/user/{id}`、`POST /api/notes/{user_note_id}/show-to-nyx`、`POST /api/notes/check-chapter-boundary`
- [ ] `pyright` strict 零报错

## 技术方案

- **涉及的 Facade / 内部类**：
  - `ReadingStore` 追加：`insert_user_note` / `list_user_notes(book_id)` / `update_user_note` / `delete_user_note` / `insert_annotation` / `list_annotations_for_notes(note_ids)`（DB 读写，复用 `conn`+`lock`；批量一次 `IN (...)`，避免逐条 N+1）
  - `ReadingFacade` 追加：笔记 CRUD 薄方法（委托 store）、`show_to_nyx`（LLM 批注）、`record_nyx_output` + `check_chapter_boundary` + `_integrate_buffer`（buffer 编排，复用 21 注入的 `llm`/`memory`/`inner_life`/`evaluator`/`bus`/`canon`；重读判定读 `ReadingStore.get_progress().read_count`、整本读完 `increment_read_count`、重读反思 `inner_life.reflect`）
  - `MemoryFacade.remember_reading`（新增，复用 `_new_memory` + `_persist_memory` 尾段）
- **关键决策**：
  - **Nyx 笔记「buffer 内存 + 记忆落 memory」**（用户已确认）：碎碎念/提问不逐条落库（参考项目的 `nyx_notes` 表**不建**）。它们由 21 的 `_mutter_reading`/`_question_reading` 产出 `content` 后调 `record_nyx_output` 追加进**内存 buffer**（`dict[book_id, list[NyxBufferEntry]]`，进程内、重启清零；associate 不进）；章末/读完整本再 LLM 整合 → 落 `memory`（`tag='reading'`）。buffer 是「为整合攒料」的瞬态，不是持久笔记。**`NyxBufferEntry` 字段**（`nyx/reading/facade.py` 模块级 dataclass，进程内 transient、不落库）：`paragraph_index: int` / `content: str` / `source: str`（`"mutter"`/`"question"`）；list 顺序即时间序、不另存时间戳。**每本书 buffer 上限 `_NYX_BUFFER_MAXLEN = 100`**（模块常量），`record_nyx_output` 超限丢弃最旧条目（防长书长时间不触章末无界增长）。
  - **用户笔记 + 批注独立两表**：用户手写笔记无 V1 现成落点（`memory` 是 Nyx 的，`tag='user'` 保留给画像），故新建 `user_notes` + `annotations`。**用户笔记与 Nyx 笔记严格分离**（用户笔记不自动进 Nyx 章末整合，对齐参考项目 C2）。
  - **章末检测靠 `is_chapter_start`**（依赖 19 已落库的 `is_chapter_start`）：19 的 `segment_html` 已在分段时识别 `h1/h2` 标题（标题并入下一段），把「本段以标题开头」记为 `Paragraph.is_chapter_start` 落库（非 `tag` 字符串），列已在 19 的 v7 定义、22 不再引入。章末判定 = `paragraphs[nyx_position+1].is_chapter_start`（下一段是新章标题 → 当前章结束），对齐参考项目 `_is_chapter_start`。不用文本正则（英文/无「章」字标题会漏）。
  - **章末整合「整合成功后才删已消费快照」**：buffer 只存「当前未整合章」的 Nyx 输出。每章末整合 `buffer[book_id]` 当前条目 → LLM 整合成功落 `memory` 后**只删本轮已消费的快照前缀**（`entries = list(...)` 拷贝快照，成功后 `del buffer[:len(entries)]`），LLM 等待期间新 append 的条目（Nyx 已翻入下一章）保留给下一轮整合；读完整本整合 buffer 全部条目（整本）→ 落一条全书记忆。**失败（LLM 异常/非法 JSON）不删，保留 buffer 供下次边界重试**。无需 per-chapter 游标。
- **整本读完 = 尼克斯自动触发**（用户已确认）：`check_chapter_boundary` 对 `nyx_position == total_paragraphs` 判定「整本读完」（`BOOK_FINISHED`）→ 后台触发整本整合（LLM 整合 buffer 全部 → 落一条全书记忆 → 清空 buffer），并返回该信号（`book_finished`）供前端提示「Nyx 也读完了」。末章无「下一章标题」，其内容由整本整合覆盖、不再单独章末整合。整合不靠用户点「读完」触发。
- **去重靠 `_persist_memory` 两层去重**：`remember_reading` 复用 `_persist_memory`（既定），其精确去重（content_hash）+ 语义去重（embedding 余弦）在重读相同章节时命中旧记忆 → `strengthen` 合并强化而非新建，天然防「大量相同记忆挤存储」。重读无需额外去重逻辑。
- **重读触发反思，按章节**（用户已确认）：`check_chapter_boundary` 先读 `get_progress(book_id).read_count`（20 的「读完几遍」计数）判定重读。首读（`read_count == 0`）只整合、不反思；重读（`read_count >= 1`）时，**每次整合（章末整合 + 整本整合）额外触发一次反思**——`await inner_life.reflect(book_id)`（与整合同后台、不阻塞翻页，correlation_id 用 `book_id` 溯源「这本书的重读触发反思」）。整本读完（`BOOK_FINISHED`）时先 `increment_read_count(book_id, book.total_paragraphs)` 再整合/反思；**reflect 判定用 `++` 前的 `read_count`**（首读 0→不反思，`++` 只影响下一轮重读判定，不把本次整本整合误判成重读）。**`++` 是确定性动作，在 `check_chapter_boundary` 判到 `BOOK_FINISHED` 时同步执行**（不挂后台整合——整合可能因 buffer 空/LLM 失败提前返回，挂那儿会漏 `++`）。**整本整合 + `++` 的幂等双保险**：`increment_read_count` 同步落 `nyx_position=total`（**跨重启**信号——重启后前端重载读到 `nyx_position==total` → waiting 不重追，重放根因消除）；同进程内再靠 `_finished_books: set[str]` 内存标记（transient、与 buffer 同生命周期）：判到 `BOOK_FINISHED` 且 `book_id not in _finished_books` 才 `get_progress`（取 `++` 前 `read_count`）→ `++` → add → spawn 整本整合；`book_id in _finished_books`（同一次读完的重复调用）直接 `return`，不 `get_progress`、不 spawn、不误触 reflect；`nyx_position` 回到 `< total`（`NONE`/`CHAPTER_END`）时 `discard`，下一遍读完再次 `++` + 整合——同一次读完的重复调用（网络重试/面板重挂载/末段多查）是纯 no-op。审美漂移因去重（重读无新 `tag='reading'` 记忆）自然为 0（23 的缩放），核心反思（性格/三观/长期欲望/自我叙事）正常更新。
  - **整合为 best-effort**：某章 Nyx 沉默（buffer 空）→ 跳过，不生成章末笔记（对齐参考 EC-003）；整合 LLM 空/失败 → 只记日志、不落空记忆、不反噬翻页主流程，且 **buffer 保留**（只在 `remember_reading` 成功后清空，下次边界重试）。
  - **「给尼克斯看」需主动触发**：Nyx 不主动读用户笔记（对齐参考 C3）。`show_to_nyx` 读笔记 + 原段落（书已删则只读笔记文字，`ON DELETE SET NULL` 兜底）→ LLM 批注 → 插 `annotations`。同一笔记多次展示 → 每次新增一行（不覆盖旧批注）。**LLM 失败/空 → 记日志返回 `None`（不落批注、不 500）**，端点据此返回 `200 null`。
  - **批注 `annotations` 只存正文**：不冗余 `paragraph_id`/`selected_text`（批注是「对笔记的回应」，段落指向已由 `user_notes.paragraph_id` 承载；Nyx 划线选文本本期不做）。
  - **`update_user_note` 先 SELECT 判存在而非 `rowcount==0`**：`rowcount` 依赖驱动/版本语义（changed vs matched），「同 tick 同内容更新」在部分实现下返回 0 会误判 404；改「先 `SELECT` 判存在 → `UPDATE` → 回读」三段，不依赖 `rowcount`。
- **LLM prompt 契约（两处）**：
  - `show_to_nyx`：复用 17 的 `build_system_prompt(canon, state)` + user prompt「给这条用户笔记写一句批注（一两句自然口语，可呼应笔记与原文）」→ `llm.complete(module="reading", output_type="reading_annotation", correlation_id=note_id)`（**无 JSON**，`content` 即批注文本）→ `evaluator.evaluate(output)` → 插 `annotations`（`insert_annotation` 返回完整 `Annotation`，`show_to_nyx` 原样返回——`{id, user_note_id, content, created_at}`）。
  - `_integrate_buffer`：用**固定 system prompt**（`_READING_NOTE_SYSTEM` 模块常量，仿 09 `_SCENE_SYSTEM`「你是尼克斯…写成第一人称记忆、只输出 JSON」风格，非 `build_system_prompt`）+ user prompt「这是你读这一章时的碎碎念/提问，整理成一条第一人称章末记忆」→ `llm.complete(module="reading", output_type="reading_note", json_mode=True, correlation_id=book_id)` → `evaluator.evaluate(output)` → `_parse_reading_note(output.content)` 拆 `{content, summary}`（JSON，仿 `_parse_scene`，结构非法抛 `ValueError`）→ `memory.remember_reading(content, summary, book_id)`。
  - 两处 `output_type`（`reading_annotation`/`reading_note`）**不进 `_VOICE_TYPES`**（`{speak, initiate_chat, think}`）——批注/整合是结构化/内部输出、非聊天语音，OOC 只走关键词档（与 `scene_memory`/`note`/`reflection` 同款）。
- **数据变更**（`_MIGRATIONS` v10，DDL 以 `nyx/db.py` 为准）：
  - **v10（本 spec）**：
    - `user_notes`：`id TEXT PRIMARY KEY`、`book_id TEXT REFERENCES books(id) ON DELETE SET NULL`、`paragraph_id TEXT REFERENCES paragraphs(id) ON DELETE SET NULL`、`content TEXT NOT NULL`、`selected_text TEXT`、`created_at REAL NOT NULL`、`updated_at REAL NOT NULL`
    - `annotations`：`id TEXT PRIMARY KEY`、`user_note_id TEXT NOT NULL REFERENCES user_notes(id) ON DELETE CASCADE`、`content TEXT NOT NULL`、`created_at REAL NOT NULL`
- **API 端点**：
  - `GET /api/notes/{book_id}` → 200 `[UserNote, ...]`（每条附 `annotations: [Annotation, ...]`，按 `created_at` DESC）
  - `POST /api/notes/user` → 请求 `{book_id, paragraph_id?, content, selected_text?}` → 201 `UserNote`（缺 `content` 422；`book_id` 不存在 404；`paragraph_id` 不存在/跨书 422）
  - `PUT /api/notes/user/{note_id}` → 请求 `{content}` → 200 `UserNote`；不存在 404
  - `DELETE /api/notes/user/{note_id}` → 204；不存在 404
  - `POST /api/notes/{user_note_id}/show-to-nyx` → 200 完整 `Annotation`（`{id, user_note_id, content, created_at}`）或 `null`（LLM 失败/空）；笔记不存在 404
  - `POST /api/notes/check-chapter-boundary` → 请求 `{book_id, nyx_position}` → 200 `{is_boundary: bool, book_finished: bool}`；`is_boundary=true` 后台触发章末整合，`book_finished=true` 后台触发整本整合（尼克斯自动，`nyx_position == total_paragraphs`）。映射：`is_boundary = (BoundaryResult == CHAPTER_END)`、`book_finished = (BoundaryResult == BOOK_FINISHED)`、`NONE` → 双 false；两者互斥（`nyx_position == total` 时无「下一段」，只判 `BOOK_FINISHED`）

## 测试要点

- [ ] 集成测试 `tests/test_reading/test_reading_facade.py`（`:memory:` + 真 `ReadingStore` + mock `llm`/`memory`）：
  - [ ] `add_user_note` 带/不带 `paragraph_id` 各一条；书不存在抛 `BookNotFoundError`、段落不存在/跨书抛 `ValueError`；`list_user_notes` 按时间排序 + 附批注（一次批量查批注、非 N+1）
  - [ ] `update_user_note` / `delete_user_note`；删 `user_note` → `annotations` CASCADE 清空
  - [ ] `show_to_nyx`：mock LLM 返回固定批注文本（`output_type="reading_annotation"`、`evaluator.evaluate` 被调）→ 插入 `annotations`；笔记 `paragraph_id` 存在 → prompt 含原段落；书已删（`book_id=NULL`）→ 只读笔记文字不报错；LLM 失败/空 → 返回 `None` 且不落批注
  - [ ] `record_nyx_output` 追加 buffer（超 `_NYX_BUFFER_MAXLEN` 丢弃最旧）；`check_chapter_boundary` 下一段 `is_chapter_start=true` → 触发章末整合（mock LLM 返回 JSON `{content, summary}`，`output_type="reading_note"`、`json_mode=True`、`evaluator.evaluate` 被调）→ `_parse_reading_note` 拆出两值 → `remember_reading(content, summary)` 被调 + buffer 清空；下一段非章首 → 不整合（`NONE`）；`nyx_position == total_paragraphs` → 整本整合（返回 `BOOK_FINISHED` + 落全书记忆）
  - [ ] 重读反思：首读（`read_count=0`）章末 → 只整合 `remember_reading`、`reflect` 未被调；重读（`read_count>=1`）章末 → 整合 + `reflect` 被调一次；`BOOK_FINISHED` → `increment_read_count`（0→1）+ 整合；`BOOK_FINISHED` 且 buffer 空/整合失败 → `read_count` 仍 ++；重复 `BOOK_FINISHED`（`nyx_position` 停在 `>= total`）→ `read_count` 不重复 `++`；重复 `BOOK_FINISHED` 在首次整合仍在 LLM 等待中 → 不 spawn 第二个整合、不误触 reflect；`nyx_position` 回到 `< total`（回翻/重读）后再到 `>= total` → 再次 `++`
  - [ ] buffer 空 → 章末不生成记忆（`remember_reading` 未被调）；整合失败（LLM 抛异常/非法 JSON）→ buffer 保留、`remember_reading` 未被调；整合成功只删已消费快照——LLM 等待期间新 append 的条目保留给下一轮
- [ ] 单元测试 `tests/test_memory/test_memory_facade.py`：`remember_reading` → `tag='reading'`、`type=LONG_TERM`、落库 1 行
- [ ] 单元测试 `_parse_reading_note`（纯函数，`tests/test_reading/test_reading_facade.py`）：合法 JSON `{"content": "…", "summary": "…"}` → `(content, summary)`；非 JSON/缺键/类型错 → 抛 `ValueError`
- [ ] 单元测试 `tests/test_reading/test_reading_store.py`：`list_annotations_for_notes` 多条笔记批注按 `created_at` 降序、空列表返回 `[]`；`update_user_note` 同 tick 同内容仍返回笔记（不误判 404）
- [ ] 契约测试 `tests/test_api/test_reading_api.py`（fake `ReadingFacade`）：
  - [ ] `POST /api/notes/user` 缺 `content` → 422；成功 → 201；`book_id` 不存在 404；`paragraph_id` 不存在/跨书 422
  - [ ] `DELETE /api/notes/user/{id}` 不存在 → 404
  - [ ] `POST /api/notes/{id}/show-to-nyx` → 200 完整 `Annotation`（`{id, user_note_id, content, created_at}`）；LLM 失败/空 → 200 `null`
  - [ ] `POST /api/notes/check-chapter-boundary` → 200 `{is_boundary}`

## 完成定义

- [ ] `ruff check` 零报错
- [ ] `pyright` 零报错
- [ ] `pytest` 全绿
- [ ] `test-inventory.md` 已更新（快照）
- [ ] ripple 同步：tech-ref §2 实体计数 +2（`UserNote`/`Annotation`）+ 枚举计数 +1（`BoundaryResult`）、§3 业务表计数 +2（`user_notes`/`annotations`）、§5 补 `ReadingFacade`（笔记方法）+ `MemoryFacade`（`remember_reading`）、§4 REST 表补 6 个笔记端点、01-types 实体计数 +2
- [ ] 用户选中文本记笔记、展示给 Nyx 得批注；Nyx 读完一章留章末记忆
