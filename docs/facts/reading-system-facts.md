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
- 组合根以窄回调向活动系统暴露未完成 EPUB 列表、单书查询、进度查询和 `read_for_activity()`；活动系统不直接访问阅读表。
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
  `Segment(text, is_chapter_start, blocks, marks)`。无块标签 fallback 也会走长段拆分；
  `script`/`style` 内容被忽略。
- `blocks` 保留标题、正文、引用、列表和预格式文本语义，`marks` 只保留粗体/斜体；
  两者 offset 都是 UTF-16 code unit，原始 HTML、属性和 EPUB CSS 不落库。旧段落的格式数组
  为空时，前端按普通正文回退。`li`、`blockquote`、`pre` 内的嵌套块继承最近语义容器，
  同时保持原有文档顺序与分段边界。
- `ReadingStore.insert_book_with_paragraphs` 在单事务内写 `books` 和 `paragraphs`；
  `content_hash` 唯一索引冲突时回滚并返回已存在的书。

## EPUB 对象完整性审计

### 入口清单

| 入口 | 产生条件 | 写入位置 |
|---|---|---|
| `POST /api/books` -> `ReadingFacade.import_book()` | 文件为 EPUB、大小合法、解析后正文非空、正文 hash 未重复 | `books` 与全部 `paragraphs` 同事务提交 |

当前没有第二个正式 EPUB 写入口；活动系统只消费阅读 Facade 暴露的书，不复制或直接写阅读表。

### 消费者清单

| 消费者 | 发现方式 | 用途 |
|---|---|---|
| 阅读器书架与正文窗口 | `list_books()`、`list_paragraphs()` | 展示、翻页、划线、书签和阅读进度 |
| 阅读整合与表达回复 | 段落查询、`ReadingIntegration`、`build_reply_context()` | 原文沉淀、书籍画像和读书提问回复上下文 |
| 委派任务 | 精确 `book_id`，执行时调用 `read_for_activity()` | 让 Nyx 读到用户指定段落 |
| 探索欲选材 | 组合根注入 `list_readable_books()`，命中后调用 `read_for_activity()` | 在未完成 EPUB 中按文件名/书名模糊匹配并读取约 6000 字符 |

### 状态迁移表

`Book` 本身没有 status 枚举；下表记录对象存在性及由 `reading_progress` 派生的可读状态，
不为填表新增状态字段。

| 当前状态 | 条件 | 下一状态 | 副作用 / 失败落点 |
|---|---|---|---|
| 不存在 | 导入成功 | 已导入、未读 | 原子写入书和段落；失败整体回滚 |
| 已导入、未读 | 用户或 Nyx 前进 | 部分已读 | 新建或更新 `reading_progress`，revision 递增 |
| 部分已读 | 首次到达书末 | 已完成 | Nyx 位置到末段，`read_count` 原子递增 |
| 已完成 | 阅读器显式重读并回到书内 | 部分已读 | `reset_completion_marker()` 增加 revision，重新允许完成计数 |
| 任意已导入状态 | 底层书籍行被删除 | 不存在 | 段落、进度和书签级联删除；笔记书/段外键置空，排队任务的 `book_id` 置空并在执行时失败 |

### Bad case 表

| 情况 | 当前处理 |
|---|---|
| 空 | 解析后没有正文时返回 400，不创建空壳书 |
| 失败 | 解析异常返回 500；书与段落事务回滚，不留下部分导入 |
| 部分完成 | 阅读块成功后才推进 Nyx 位置；未到书末仍由 `list_readable_books()` 返回给活动选材 |
| 乱序 | 进度以 revision 做 CAS；前端和后台冲突时重读服务端状态，普通路径只允许 Nyx 位置前进 |
| 重放 | 相同正文 hash 幂等定位已有书并由 API 返回 409；原文 pending 重放按 `book:` 来源内去重 |
| 删除 | 当前没有公开书籍删除 API；若底层删除发生，外键按上表处理，旧活动读取返回书不存在，排队任务进入失败态 |
| 新消费者 | 活动选材通过 `list_readable_books()` 枚举 `POST /api/books` 产生的全部未完成 EPUB；这是活动读书的唯一候选库 |
| 无本地匹配 | 活动系统进入既定搜索/默认路径，不以“最近上传 EPUB”冒充主题命中 |

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
- `advance_nyx_position` 只在目标更靠后时更新 Nyx 位置，保留服务端当前的用户位置、速度和读完次数；供后台活动与前端并发保存进度时使用。
- 前端 `readerStore` 保存 `progressRevision`；同一本书的写入串行排队，409 或其他
  写入失败会先重新读取服务端进度，再用最新 revision 重试。普通写入取本地/服务端
  Nyx 位置最大值，显式“重读”仍允许回退。翻页跨多段的冲动请求也按书串行排队。
- 普通翻页走 `syncPosition` 并只为实际前进段落评估冲动；从历史划线或书签定位走
  `jumpToPosition`，同样持久化位置、按需重拉窗口和恢复 Nyx 追赶，但不为跨过的段落
  补发冲动。

## 阅读冲动与陪读输出

- `evaluate_paragraph` 只接受前进段落；回翻、缺书或缺段返回空列表。
- 段落特征、六驱动、五种行为复合、阈值和单调钟冷却均在
  `nyx/reading/impulse.py` 的同步纯函数中计算；四类提问只保留超过阈值且分数最高的
  一个，并共享 `ReadingFacade` 进程级 180 秒冷却。
- 同一段最多产生一个 `question_*`/`quote_question`；`associate` 的 60 秒冷却和
  `mutter` 的 30 秒冷却独立保留，所以它们仍可与提问并存。提问冷却在恰好 180 秒时
  恢复，服务重启后清零；冷却只在问题成功提交后开始，失败/空输出/非法问句不会
  消耗预算，后台进行中的问题只占用一个待定名额。
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

## 原文沉淀与提问回复

- 前进到新段落时，阅读后台任务还会调用 `ReadingIntegration.sediment()`；同一本书用
  per-book lock 串行。原文累计到 6000 字符立即沉淀，章末/书末 flush 余量；超长单段用
  `{paragraph_index, char_offset}` cursor 拆块，段落表中的原文不拆改。
- `books.memory_state` 保存 cursor、滚动 profile 和 pending。每块先生成 profile 与最多 5
  条知识，再保存 pending、写带 `book:` topic 的 knowledge，最后推进 cursor 并清 pending；
  崩溃恢复重放 pending，统一来源内去重吸收重复。
- `read_for_activity()` 从持久化 Nyx 位置读到明确目标，或为普通探索选择约 6000 字符的
  下一块；它以 `flush=True` 复用同一原文沉淀管道，成功后才单调推进 Nyx 位置，不写陪读 buffer。
- profile 包含滚动摘要、最多 5 个主题和固定内容类别。fiction/essay/unknown 使用来源归因，
  书中第一人称不归给用户或 Nyx；同名书按 `book_id` 隔离。
- 用户回答 durable 读书提问时，`ReadingFacade.build_reply_context()` 返回三层资料：该书
  来源内相关 knowledge Top 5 的事实正文、触发提问的完整原段落，以及书名/作者/profile。
  组合根以窄回调注入表达门面，FAST 和 SLOW 共用，避免短回复“已读乱回”。

## 笔记、划线、书签与整合

- 用户笔记和批注分别存于 `user_notes`、`annotations`；笔记正文和选中文本都限制
  4000 字符。
- 纯划线复用 `user_notes`，以空 `content` 加段落、选中文本和 UTF-16 闭开 offset 表示；
  后端严格校验 offset 切出的原文，完全相同的纯划线幂等。旧的无 offset 引用仍作为
  普通笔记显示，但不绘制到正文。
- `bookmarks` 独立存储，同书同段唯一；列表返回段号和原文预览。前端只在当前书已加载
  的有效划线中按 `selected_text` 做大小写不敏感过滤，点击划线或书签统一调用
  `jumpToPosition`。书签列表请求的成功/失败回包仅在请求书仍是当前书时落 store；旧书
  晚到回包丢弃。正文重叠划线以排序端点扫描渲染，不按原子区间反复全量扫描。
  `NotePanel` 不展示空正文的纯划线。
- `GET /api/notes/{book_id}` 先确认书存在，不存在返回 404；批注通过一次 IN 查询
  批量拼装，避免 N+1。
- Nyx 的碎碎念和提问只进入进程内 `ReadingIntegration.buffer`，每本最多 100 条；
  不逐条写入数据库。
- 章末或整本边界创建后台整合任务；同一本书同一时间只有一个整合任务。
- 整合 snapshot 先调用 LLM、评估、解析 JSON，再 `remember_reading`；重读时还要成功
  受理 `REFLECTION(reason=reading_revisit)`，事件保留 `book_id` 与本轮 summary evidence。所有必需步骤成功后才删除 snapshot，失败保留供再次边界
  调用重试。
- prompt 中整合材料总长度限制为 12000 字符。
- `BOOK_FINISHED` 的读完计数先同步提交，buffer 为空或整合失败都不回滚读完事实。
- 进程内标记加持久化 `nyx_position=total/read_count>=1` 共同防止重复计数；
  回到书内后重新允许下一次完成。

## 数据库迁移与测试

- 当前阅读表由 `nyx/db.py` 的 v7-v10、v18 和 v28-v30 迁移建立/升级；v18 增加
  `reading_progress.revision`，v28 为 `books` 增加内部 `memory_state` checkpoint，
  v29 增加段落格式 JSON，v30 增加划线 offset 和书签表。v31 的 `assigned_task.book_id`
  外键指向 `books`，删除书后置空并由任务执行显式失败。
- 后端阅读测试位于 `tests/test_reading/`，API 契约测试位于
  `tests/test_api/test_reading_api.py`；前端阅读测试主要位于
  `frontend/tests/stores.test.ts`、`frontend/tests/api.test.ts`、
  `frontend/tests/readerView.test.tsx`。
