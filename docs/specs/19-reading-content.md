# 阅读内容导入（EPUB → books/paragraphs）

> 范围：`nyx/reading/` 模块的**内容导入**部分——EPUB 文件解析成段落（`paragraphs` 表）+ 元信息（`books` 表），并提供导入 API。只管「EPUB 进、两张表出」，不含书架/进度（20-reading-progress）、冲动引擎（21）、笔记（22）、审美（23）。
> spec 只定义契约（签名 + 语义 + 决策），不内联完整代码；代码唯一事实来源是 `nyx/` 源文件。

## 元信息

- **前置依赖**：01-types（dataclass 约定）、04-db（`_MIGRATIONS` 版本化迁移）
- **实现文件**：`nyx/types.py`（新增 `Book`/`Paragraph`）、`nyx/db.py`（`_MIGRATIONS` 追加 v7）、`nyx/reading/__init__.py`、`nyx/reading/segmenter.py`（`Segment` + `segment_html`）、`nyx/reading/epub.py`（`EpubResult` + `parse_epub`）、`nyx/reading/store.py`（`ReadingStore`）、`nyx/reading/facade.py`（`ReadingFacade` + `DuplicateBookError`）、`nyx/main.py`（端点 + 组合根装配）
- **反向修订 18-api**：18-api 现行只含 14 个端点、`build_app_context`/`_App` 无 reading；本 spec 负责**新增** `ReadingFacade` 装配 + `POST /api/books` 端点（见「组合根装配」）。18-api 是「被扩展」的既有 spec，不是前置依赖。

## 用户故事

> 作为用户，我想要导入一本 EPUB 电子书，让 Nyx 把它解析成以段落为单位的正文入库，以便在阅读页和 Nyx 同步陪读。

## 验收标准

- [ ] `nyx/db.py` 的 `_MIGRATIONS` 追加 v7（`books` + `paragraphs` 两表 DDL，见「数据变更」）；`connect(":memory:")` 后两表存在
- [ ] `nyx/types.py` 含 `Book`：`id/title/author/filename/content_hash/total_paragraphs/created_at/updated_at`（**8 字段全非 Optional**——`books` 表所有列 `NOT NULL`，`author`/`filename` 的 `DEFAULT ''`、`total_paragraphs` 的 `DEFAULT 0` 只作用于 INSERT、不改变可空性，04-db「X | None ⟺ DDL 可空」约定下对应非 Optional `str`/`int`）
- [ ] `nyx/types.py` 含 `Paragraph`：`id/book_id/index/text/is_chapter_start`（`index` 从 1 起、per book 连续；`is_chapter_start` 供 22 章末检测用）
- [ ] `nyx/reading/segmenter.py` 含 `Segment = NamedTuple("Segment", [("text", str), ("is_chapter_start", bool)])`（**定义在 segmenter.py**，`epub.py`/`store.py` 从此 import）与纯函数 `segment_html(html: str) -> list[Segment]`（同步、无 IO、无 LLM）
- [ ] `nyx/reading/epub.py` 含 `EpubResult`（dataclass，定义在 epub.py：`title: str`/`author: str`/`segments: list[Segment]`/`content_hash: str`）与 `parse_epub(data: bytes) -> EpubResult`（同步、无 LLM）
- [ ] `nyx/reading/store.py` 含 `ReadingStore(db: Database)`：`insert_book` / `insert_paragraphs(book_id, segments: list[Segment])` / `find_by_hash`（`async`、用注入 `Database` 的 `conn`+`lock`）
- [ ] `nyx/reading/facade.py` 含 `DuplicateBookError(existing_book_id: str, title: str)`（定义在 facade.py，同 `ConfigError` 定义在 config.py 的先例）+ `ReadingFacade(store: ReadingStore).import_book(filename: str, data: bytes) -> Book`（`async`）；正文重复时抛 `DuplicateBookError`
- [ ] `POST /api/books` 端点：multipart 字段 `file`（`UploadFile`，`.epub` 字节）→ 201 `Book`；重复 → 409 含 `existing_book_id`+`title`；非 `.epub`/超限/空正文 → 400；解析失败（含 DRM）→ 500
- [ ] 段落 `index` 从 1 连续递增；`UNIQUE(book_id, index)` 生效
- [ ] `pyright` strict 零报错

## 技术方案

### 涉及的 Facade / 内部类

- `ReadingStore`（`nyx/reading/store.py`）——books/paragraphs 的 DB 读写，唯一写路径；方法：`insert_book(title, author, filename, content_hash, total_paragraphs) -> Book`、`insert_paragraphs(book_id, segments: list[Segment]) -> None`（`Segment.is_chapter_start` 落 `paragraphs.is_chapter_start`，bool 存 1/0；读回时 `bool(...)` 还原，读侧在 20）、`find_by_hash(content_hash) -> Book | None`
- `ReadingFacade`（`nyx/reading/facade.py`）——`import_book(filename, data)`：`parse_epub` → `find_by_hash` 去重 → `insert_book` + `insert_paragraphs` → 返回 `Book`；重复抛 `DuplicateBookError`（域异常，端点映射 409）
- `parse_epub`（`nyx/reading/epub.py`）——ebooklib 读 EPUB → 遍历 spine 文档 → 逐文档 `segment_html` → 提取 `dc:title`/`dc:creator` → 全文 SHA-256；**同步**（CPU + ebooklib 都是同步阻塞），Facade 用 `asyncio.to_thread(parse_epub, data)` 卸载，不阻塞事件循环
- `segment_html`（`nyx/reading/segmenter.py`）——纯函数，HTML → `list[Segment]`（分段规则见下）

### 组合根装配（本 spec 反向扩展 18-api）

- `build_app_context` 里：`reading = ReadingFacade(ReadingStore(db))`（P1 仅注入 store；21/22 各自 spec 追加 inner_life/desire/memory/llm/evaluator/bus/canon 依赖时同步扩构造签名 + 此处装配）。构造位置在 `inner_life` 装配之后即可（19 无依赖），`_App` 加 `reading: ReadingFacade` 字段。
- `build_app` 里注册 `POST /api/books` 端点闭包调 `app.reading.import_book(...)`（薄封装，错误映射见「API 端点」）。

### 关键决策

- **新增 `ebooklib` 依赖**（用户已确认）：`pyproject.toml` 加 `ebooklib`；是唯一的现成 Python EPUB 解析器，与参考项目 S03 同款。
- **`material` 与 `books` 是并行双书库，不合并不替换**（设计文档 §5.5）：`material` + `POST /api/upload` + `GET /api/materials` = **Nyx 自己读的分块文本**（读书活动，`MaterialStore` 管），本 spec 不动；`books`/`paragraphs` + `POST /api/books` + `GET /api/books` = **用户陪读的章节段落**。结构不同各管各的（`material` 按字符分块、`books` 按段落分节）。前端书架读 `/api/books`（用户陪读），Nyx 读书活动仍走 `/api/materials`，互不干扰。
- **字节进、不落盘**：V1 无 Tauri 文件对话框，复用 `POST /api/upload` 的 multipart `UploadFile` 模式——前端选 `.epub` 后读字节上传；解析出的段落全量落库，EPUB 原始字节**不持久化**（没有 `source_path`）。`books` 表用 `filename`（消毒后的上传文件名）做展示。
- **去重靠 `content_hash`**：`content_hash = sha256("\n".join(所有段落文本))`（对分段后正文哈希，改名重导仍命中）；`books.content_hash` 加普通索引 `idx_books_content_hash`（**非 UNIQUE**——去重是 Facade 业务规则返回 409，非硬约束），与 `memory.content_hash` 现有模式一致。
- **相对参考项目 S03 的收敛**（设计文档 §3.1 只列「标题/作者」，其余砍掉）：不存 `book_type`、不存封面（`cover_path`）、不存 `source_path`/`source_url`；`paragraphs` 不存 `tag`/`raw_html`（阅读页纯文本渲染，`tag` 只作分段器内部合并判据、不进输出）。
- **段落分段规则**（照搬参考 S03 `segment_html` 语义，输出收窄为纯文本）：块级元素（`p`/`h1-h6`/`blockquote`/`li`/`pre`）各成段；标题与紧邻 `p` 合并（`"标题\n正文"` 一段）；同层连续 `li` 合并（换行分隔）；连续短 `p`（累计 < 100 字符）合并；单段 > 3000 字符在最后一个句号处拆；无结构化标签则全文一段。**不保留 `tag` 字符串输出**，但「以 `h1`/`h2` 开头的段」标 `Segment.is_chapter_start=True`（22 章末检测用）。
- **主键用 uuid TEXT**（匹配 V1 全库 TEXT PK 约定，非参考项目 INTEGER AUTOINCREMENT）：`books.id`/`paragraphs.id` 为 `TEXT`（uuid4），`paragraphs` 加 `UNIQUE(book_id, index)` 保序 + 完整性。
- **ebooklib 解析契约**（`parse_epub` 内部）：`epub.read_epub(io.BytesIO(data))` → 按 `book.spine`（`list[(idref, linear)]`）**阅读序**遍历，`linear == 'no'` 跳过（辅助项）；每 `idref` 经 `book.get_item_with_id(idref)` 取 item，`item.get_type() != ebooklib.ITEM_DOCUMENT` 跳过（图片/CSS 等非 HTML 项不进正文）；`item.get_content()` 返回 bytes → `decode("utf-8", errors="replace")`（EPUB HTML 约定 UTF-8，坏编码兜底不抛）→ `segment_html` 收集 segments。标题/作者：`book.get_metadata("DC", "title")` 取第一个元组 `[0]`，空则回退 filename；`get_metadata("DC", "creator")` 同理，空则回退 `""`。
- **空正文报错**：`parse_epub` 得 0 段（空 EPUB/无 HTML 正文）→ `import_book` 抛 `ValueError("EPUB 无正文")`，端点映射 400（可导入但无正文，属输入问题，与「非 epub/超限」同类）；**不插 0 段落的 book**（`total_paragraphs=0` 会让书架出现空书、`UNIQUE(book_id,index)` 无行语义诡异）。
- **大小上限** `_MAX_EPUB_BYTES = 50 * 1024 * 1024`（50MB，decision 可推翻）；端点**分块读**（对齐 `/api/upload` 的 `while chunk := await file.read(1 << 20)` 模式），累计超 `_MAX_EPUB_BYTES` 立即 400 中断**不继续读剩余**；通过后 `b"".join` 得完整 bytes 交 `parse_epub`（此时总量已 ≤ 50MB）。

### 数据变更（`_MIGRATIONS` v7，DDL 以 `nyx/db.py` 为准）

- `books`：`id TEXT PRIMARY KEY`、`title TEXT NOT NULL`、`author TEXT NOT NULL DEFAULT ''`、`filename TEXT NOT NULL DEFAULT ''`、`content_hash TEXT NOT NULL`、`total_paragraphs INTEGER NOT NULL DEFAULT 0`、`created_at REAL NOT NULL`、`updated_at REAL NOT NULL`；`CREATE INDEX idx_books_content_hash ON books(content_hash)`
- `paragraphs`：`id TEXT PRIMARY KEY`、`book_id TEXT NOT NULL REFERENCES books(id) ON DELETE CASCADE`、`index INTEGER NOT NULL`、`text TEXT NOT NULL`、`is_chapter_start INTEGER NOT NULL DEFAULT 0`、`UNIQUE(book_id, index)`

### API 端点

- `POST /api/books`：multipart `UploadFile`，字段名 `file`（`file: UploadFile = File(...)`，对齐 `/api/upload` 同名字段）。端点内顺序：
  1. 扩展名非 `.epub`（`Path(filename).suffix.lower() != ".epub"`）→ 400；
  2. 分块读，累计超 `_MAX_EPUB_BYTES` → 400「文件过大」（不整读超限文件）；
  3. `import_book(filename, data)`：`DuplicateBookError` → 409 `{"existing_book_id", "title"}`；`ValueError`（空正文）→ 400；ebooklib 抛异常（含 DRM）→ 500「EPUB 解析失败」；
  4. 成功 201 返回 `Book` 的 JSON（dataclass 直序列化，无 StrEnum 字段）。

## 测试要点

- [ ] 单元测试 `tests/test_reading/test_segmenter.py`（纯函数，无 IO）：
  - [ ] 标题+紧邻段合并：`<h2>第一章</h2><p>正文</p>` → 一段 `"第一章\n正文"` 且 `is_chapter_start=True`
  - [ ] 同层 `li` 合并：3 条 `<li>` → 1 段（换行分隔）
  - [ ] 短 `p` 合并、超长段拆分、无标签回退各一例
- [ ] 单元测试 `tests/test_reading/test_epub.py`（构造内存 EPUB 或 fixture 字节）：`parse_epub` 返回 `EpubResult`，`content_hash` 稳定（同一 bytes 两次哈希一致）；`title`/`author` 提取正确、空 metadata 回退；spine 里非 `ITEM_DOCUMENT`（图片）被跳过
- [ ] 集成测试 `tests/test_reading/test_reading_facade.py`（`:memory:` + 真 `ReadingStore`）：
  - [ ] `import_book` 首次 → `books` 1 行 + `paragraphs` N 行、`index` 连续从 1 起、返回 `Book.total_paragraphs == N`
  - [ ] 重复导入（同 `content_hash`）→ 抛 `DuplicateBookError`（含 `existing_book_id`/`title`）
  - [ ] 空 segments → 抛 `ValueError`（不插 book）
  - [ ] 删 book → `paragraphs` CASCADE 清空
- [ ] 契约测试 `tests/test_api/test_reading_api.py`（`httpx.AsyncClient` + `ASGITransport` + fake `ReadingFacade`）：`POST /api/books` 成功 201；重复 409；非 `.epub`/超限/空正文 400；解析失败 500
- [ ] E2E：前端选 `.epub` → 书架出现新书（手动，阶段 3）

## 完成定义

- [ ] `ruff check` 零报错
- [ ] `pyright` 零报错
- [ ] `pytest` 全绿
- [ ] `test-inventory.md` 已更新（快照）
- [ ] ripple 同步：tech-ref §2 实体计数 +2（`Book`/`Paragraph`）、§3 业务表计数 +2（`books`/`paragraphs`）+ 索引 +1（`idx_books_content_hash`）、§5 补 `ReadingFacade`（`import_book`）、§7 补 `reading/` 包（`__init__`/`segmenter`/`epub`/`store`/`facade`）、§4 REST 表补 `POST /api/books`、01-types 实体计数 +2
- [ ] 用户能导入一本 EPUB，书架上出现该书
