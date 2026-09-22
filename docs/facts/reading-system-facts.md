# 阅读系统事实摘要

> 本文只记录当前源码已经实现的事实，供无关代码快速定位。它不是契约，也不替代
> [`../specs/12-reading-system.md`](../specs/12-reading-system.md)。契约和源码不一致时，
> 先识别差异并按项目文档同步规则处理。

## 模块与装配

- 阅读源码位于 `nyx/reading/`，核心入口是 `ReadingFacade`。
- 组合根在 `nyx/app_context.py` 构造 `ReadingStore`、`ReadingFacade` 并注入：
  `InnerLifeFacade`、`DesireFacade`、`MemoryFacade`、`LlmClient`、`Evaluator`、
  `EventBus`、canon 和 `ExpressionFacade`。
- `ReadingFacade` 还持有 `ReadingCompanion` 和 `ReadingIntegration`。
- `nyx/main.py` 关停时先停止阅读后台任务接收，再等待阅读任务 `drain()`，最后关闭总线；
  总线最终关闭共享数据库。

## 内容导入

- `POST /api/books` 接收 `.epub` multipart 文件；文件名会去路径、控制字符和
  `<>"`，最长 255 字符。
- API 分块读取，压缩包字节上限为 50 MiB；`parse_epub` 预检 ZIP 解压总大小上限
  100 MiB。
- `parse_epub` 在工作线程执行，沿 EPUB spine 读取文档，跳过 `linear=no` 和非
  `ITEM_DOCUMENT` 项，正文交给 `segment_html`。
- `segment_html` 按块标签、标题合并、连续列表合并、短段合并和长段拆分生成
  `Segment(text, is_chapter_start)`。无块标签 fallback 也会走长段拆分。
- `ReadingStore.insert_book_with_paragraphs` 在单事务内写 `books` 和 `paragraphs`；
  `content_hash` 唯一索引冲突时回滚并返回已存在的书。

## 进度

- `reading_progress` 一书一行，保存 `user_position`、`nyx_position`、
  `reading_speed`、`read_count`、`updated_at` 和 `revision`。
- 进度位置从 1 开始，必须不超过该书 `total_paragraphs`；默认进度是
  `user_position=1`、`nyx_position=1`、`reading_speed=50`、`read_count=0`、
  `revision=0`、`updated_at=0`。
- `PUT /api/progress/{book_id}` 必须携带 `expected_revision`。首次写入要求 0；
  后续写入使用服务端返回的 revision。条件更新失败返回 409，不覆盖新状态。
- 成功写入返回完整 `ReadingProgress`，并把 revision 加 1。
- 整本读完的 `increment_read_count` 是独立原子 UPSERT：`read_count + 1`、
  `nyx_position=total`、revision 加 1。
- 回到书内时 `reset_completion_marker` 把持久化的 `nyx_position` 改回当前位置并
  增加 revision，供重读跨重启识别。
- 前端 `readerStore` 保存 `progressRevision`；同一本书的写入串行排队，409 或其他
  写入失败会先重新读取服务端进度，再用最新 revision 重试。翻页跨多段的冲动请求
  也按书串行排队。

## 阅读冲动与陪读输出

- `evaluate_paragraph` 只接受前进段落；回翻、缺书或缺段返回空列表。
- 段落特征、六驱动、五种行为复合、阈值和单调钟冷却均在
  `nyx/reading/impulse.py` 的同步纯函数中计算；四类提问只保留超过阈值且分数最高的
  一个，并共享 `ReadingFacade` 进程级 180 秒冷却。
- 同一段最多产生一个 `question_*`/`quote_question`；`associate` 的 60 秒冷却和
  `mutter` 的 30 秒冷却独立保留，所以它们仍可与提问并存。提问冷却在恰好 180 秒时
  恢复，服务重启后清零。
- 后台分派按书使用最多 2 个并发槽，同一 `(book_id, paragraph_index)` 在途时不重复
  创建任务；任务由 `ReadingFacade` 追踪。
- 原文进入陪读 prompt 前截断到 6000 字符，并标明是原文材料而非指令。
- `mutter`/`question` 的 LLM 产出只有非空且提问结构合法时才继续。
- `ReadingCompanion.commit_reading_question` 存在时，提问的 interaction attempt、
  `ASK` 事件和 `READING_QUESTION` 事件在同一数据库事务中提交；提交失败不会留下
  单独的 attempt。兼容 fake 或旧注入对象时才退回旧的分步路径。
- 提问成功后才进入阅读 buffer；碎碎念在事件发布成功后才进入 buffer。
- 联想读取 `MemoryFacade.search`，每段最多广播 3 条 `READING_ASSOCIATION`，不进入
  Nyx 读书 buffer。

## 笔记与整合

- 用户笔记和批注分别存于 `user_notes`、`annotations`；笔记正文和选中文本都限制
  4000 字符。
- `GET /api/notes/{book_id}` 先确认书存在，不存在返回 404；批注通过一次 IN 查询
  批量拼装，避免 N+1。
- Nyx 的碎碎念和提问只进入进程内 `ReadingIntegration.buffer`，每本最多 100 条；
  不逐条写入数据库。
- 章末或整本边界创建后台整合任务；同一本书同一时间只有一个整合任务。
- 整合 snapshot 先调用 LLM、评估、解析 JSON，再 `remember_reading`；重读时还要成功
  受理 `REFLECTION` 事件。所有必需步骤成功后才删除 snapshot，失败保留供再次边界
  调用重试。
- prompt 中整合材料总长度限制为 12000 字符。
- `BOOK_FINISHED` 的读完计数先同步提交，buffer 为空或整合失败都不回滚读完事实。
- 进程内标记加持久化 `nyx_position=total/read_count>=1` 共同防止重复计数；
  回到书内后重新允许下一次完成。

## 数据库迁移与测试

- 当前阅读表由 `nyx/db.py` 的 v7-v10 和 v18 迁移建立/升级；v18 增加
  `reading_progress.revision`。
- 后端阅读测试位于 `tests/test_reading/`，API 契约测试位于
  `tests/test_api/test_reading_api.py`；前端阅读测试主要位于
  `frontend/tests/stores.test.ts`、`frontend/tests/api.test.ts`。
