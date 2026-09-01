# 阅读进度与书架（reading_progress + 分页 + 书架列表）

> 范围：`nyx/reading/` 模块的**进度与书架**部分——书架列表、段落分页读取、用户/Nyx 双位置进度持久化。Nyx 逐段追赶的定时器在前端（见 `docs/frontend/06-reading-panel.md`），本 spec 只提供后端读写契约。
> spec 只定义契约（签名 + 语义 + 决策），不内联完整代码；代码唯一事实来源是 `nyx/` 源文件。

## 元信息

- **前置依赖**：19-reading-content（`books`/`paragraphs` 表 + `ReadingStore`）、04-db（`_MIGRATIONS` v9）
- **反向修订 18-api**：原 18-api 无 reading 端点（19 已加 1 个 `POST /api/books`）；`ReadingFacade` 构造与 `_App.reading` 字段已由 19 反向扩展完成，本 spec 在 `build_app` **追加** 4 个端点闭包（`GET /api/books` / `GET /api/books/{book_id}/paragraphs` / `GET /api/progress/{book_id}` / `PUT /api/progress/{book_id}`）。18-api 是「被扩展」的既有 spec，不是前置依赖。
- **实现文件**：`nyx/types.py`（新增 `ReadingProgress`/`BookListItem`）、`nyx/db.py`（`_MIGRATIONS` 追加 v9）、`nyx/reading/store.py`（追加进度/列表/分页方法）、`nyx/reading/facade.py`（追加读方法 + `BookNotFoundError`）、`nyx/main.py`（端点）

## 用户故事

> 作为用户，我想要书架列出已导入的书和各自读到的位置、点开后从上次位置继续、翻页时读到对应段落，以便和 Nyx 在同一本书里相遇。

## 验收标准

- [ ] `nyx/db.py` 的 `_MIGRATIONS` 追加 v9（`reading_progress` 表，见「数据变更」）
- [ ] `nyx/types.py` 含 `ReadingProgress`：`book_id/user_position/nyx_position/reading_speed/read_count/updated_at`（全非 Optional；keyed by `book_id`，无独立 id）
- [ ] `nyx/types.py` 含 `BookListItem`：`id/title/author/filename/total_paragraphs/user_position/last_read_at`（`last_read_at: float | None`——未读 `None`；`user_position: int` 的 0 是未读哨兵；其余全非 Optional）
- [ ] `ReadingStore` 追加 `find_book(book_id)` / `list_books` / `list_paragraphs(book_id, from_idx, to_idx)` / `get_progress(book_id)` / `upsert_progress(...)` / `increment_read_count(book_id, nyx_position)`（全 `async`）
- [ ] `ReadingFacade` 追加 `list_books() -> list[BookListItem]` / `list_paragraphs(book_id: str, from_idx: int, to_idx: int) -> list[Paragraph]` / `get_progress(book_id: str) -> ReadingProgress` / `save_progress(book_id: str, user_position: int, nyx_position: int, reading_speed: int) -> ReadingProgress`（全 `async`）
- [ ] `GET /api/books` → 书架列表（按 `last_read_at` DESC，未读排后按 `created_at` DESC）
- [ ] `GET /api/books/{book_id}/paragraphs?from=&to=` → 段落范围（`index` 升序）
- [ ] `GET /api/progress/{book_id}` → 进度；书不存在 404；无记录返回默认 `{user_position:1, nyx_position:1, reading_speed:50, read_count:0}`
- [ ] `PUT /api/progress/{book_id}` → 书不存在 404；否则 UPSERT（存在 UPDATE、不存在 INSERT），返回 `{ok: true}`
- [ ] `pyright` strict 零报错

## 技术方案

- **涉及的 Facade / 内部类**：
  - `ReadingStore` 追加六方法（DB 读写，复用注入 `Database` 的 `conn`+`lock`）：
    - `find_book(book_id) -> Book | None`——按 `book_id` 单行查，供 facade 判书存在
    - `list_books() -> list[BookListItem]`——`books LEFT JOIN reading_progress`，`last_read_at = reading_progress.updated_at`（未读为 `None`）、`user_position = COALESCE(reading_progress.user_position, 0)`（未读 0）
    - `list_paragraphs(book_id, from_idx, to_idx) -> list[Paragraph]`——`WHERE book_id=? AND index BETWEEN ? AND ? ORDER BY index ASC`；`is_chapter_start`（INTEGER 0/1）读回 `bool(...)` 还原（19 已定「读侧在 20」，此处落实）
    - `get_progress(book_id) -> ReadingProgress | None`——按 `book_id` 单行查
    - `upsert_progress(book_id, user_position, nyx_position, reading_speed) -> ReadingProgress`——`INSERT ... ON CONFLICT(book_id) DO UPDATE`（`updated_at = now`；**不碰 `read_count`**，进度写回不得重置重读计数）
    - `increment_read_count(book_id, nyx_position) -> ReadingProgress`——UPSERT 式 `++`：`INSERT INTO reading_progress(book_id, nyx_position, read_count, updated_at) VALUES (?, ?, 1, now) ON CONFLICT(book_id) DO UPDATE SET nyx_position = excluded.nyx_position, read_count = read_count + 1, updated_at = excluded.updated_at`（有行 `+1`、无行建默认行 `read_count=1`；`user_position`/`reading_speed` 走 DDL DEFAULT，`nyx_position` 显式落 `total`——22 跨重启幂等信号），供 22 的整本读完 `++`（首读 0→1、重读 1→2）；无进度行（前端从未 `save_progress`）也照常 `++`，不依赖先建行
  - `ReadingFacade` 追加四个薄读方法（委托 store）：`list_books` 直通；`list_paragraphs`/`get_progress`/`save_progress` 先 `find_book` 判书存在、不存在抛 `BookNotFoundError(book_id)`（端点映射 404）；`list_paragraphs` 再判 `to_idx > book.total_paragraphs` → 抛 `ValueError`（端点 422，越界不截断）；`get_progress` 无进度行时返回默认 `ReadingProgress(book_id, 1, 1, 50, 0, 0.0)`（`read_count=0`、`updated_at=0.0` 从未保存哨兵），非 None
- **关键决策**：
  - **进度 1:1 书**：`reading_progress` 用 `book_id` 作 PK（单行 per 书，`ON DELETE CASCADE`），与 V1 `material`（path PK）/`desire_value`（type PK）同款「key 即主键」模式，不另起自增 id
  - **不存 `nyx_status`**：`reading/waiting/idle` 由 `nyx_position >= user_position` 派生（waiting），不落库——对齐 V1「state 是 value 派生」哲学；`nyx_status` 归前端 `readerStore` 派生态（见 06-reading-panel）
  - **不冗余 `last_read_at` 列**：`books` 不加 `last_read_at`，书架排序取 `reading_progress.updated_at`（LEFT JOIN），未读 `NULL` 排后按 `books.created_at`——避免与进度表重复
  - **不存 `user_percent`**：进度百分比由前端 `user_position / total_paragraphs` 派生，后端只回 `user_position` + `total_paragraphs`
  - **不存内容预览**：设计文档 §3.1 只要求「列表 + 阅读进度展示」，不做段落首字预览（省一次 JOIN，参考 S04 的 `preview` 砍掉）
  - **书不存在 404、无进度给默认**：`list_paragraphs`/`get_progress`/`save_progress` 先 `find_book` 判书存在（书不存在抛 `BookNotFoundError` → 端点 404，`PUT` 防 FK 撞 500、`GET paragraphs` 对齐已有 404）；书存在但无进度行 → `get_progress` 返回默认进度（200），不 404——前端无需特殊分支（与参考 S05 的「404→前端兜底」等价，但更简单）
  - **`user_position`/`nyx_position` 1 起，`BookListItem.user_position` 的 0 是未读哨兵**：落库的 `reading_progress.user_position`/`nyx_position` 恒 ≥ 1（`DEFAULT 1`，与 `Paragraph.index` 从 1 起对齐）；`BookListItem.user_position` 由 LEFT JOIN 的 `COALESCE(reading_progress.user_position, 0)` 派生，0 只表示「无进度行（未读）」，不是可落库值。二者语义不同、不冲突。
  - **`reading_speed` 默认 50**（字符/秒，10–200 可调），随进度行持久化（per 书）；端点 pydantic 模型 `ge=10, le=200` 校验（超界 422）
  - **`read_count` 是「读完几遍」计数，只由整本读完 `++`**（22 的 `check_chapter_boundary` 对 `nyx_position == total_paragraphs` 时 `increment_read_count`）：首读 0→1、重读 1→2、…。「重读」判定 = `read_count >= 1`（读过至少一遍）。`save_progress`/`PUT /api/progress` 写回进度**不碰** `read_count`（UPSERT 只更新 position/speed/updated_at），否则用户翻页写回会把重读计数冲掉。
- **数据变更**（`_MIGRATIONS` v9，DDL 以 `nyx/db.py` 为准）：
  - `reading_progress`：`book_id TEXT PRIMARY KEY REFERENCES books(id) ON DELETE CASCADE`、`user_position INTEGER NOT NULL DEFAULT 1`、`nyx_position INTEGER NOT NULL DEFAULT 1`、`reading_speed INTEGER NOT NULL DEFAULT 50`、`read_count INTEGER NOT NULL DEFAULT 0`、`updated_at REAL NOT NULL`
- **API 端点**：
  - `GET /api/books` → 200 `[BookListItem, ...]`，`ORDER BY last_read_at IS NULL ASC, last_read_at DESC, created_at DESC`（`last_read_at` = `reading_progress.updated_at` 别名；未读排后按 `created_at` DESC，已读按 `last_read_at` DESC）
  - `GET /api/books/{book_id}/paragraphs?from=&to=` → 200 `[{id, book_id, index, text, is_chapter_start}, ...]`（`index` 升序、`is_chapter_start` bool）；`from`/`to` 必填（缺 → 422）、`from >= 1`、`to >= from`（否则 422）；书不存在 → 404；`to > total_paragraphs` → 422（越界不截断；`from > total` 因 `to >= from` 已覆盖）
  - `GET /api/progress/{book_id}` → 书不存在 404；无记录 200 默认 `{user_position:1, nyx_position:1, reading_speed:50, read_count:0}`；有记录 200 落库值
  - `PUT /api/progress/{book_id}` → 书不存在 404；否则请求体 pydantic 模型 `{user_position, nyx_position, reading_speed}`（`user_position`/`nyx_position` `ge=1`、`reading_speed` `ge=10, le=200`；缺键/类型错/超界 → 422）→ 200 `{ok: true}`；**不写 `read_count`**（重读计数只由 22 整本读完 `++`）

## 测试要点

- [ ] 集成测试 `tests/test_reading/test_reading_facade.py`（`:memory:` + 真 `ReadingStore`）：
  - [ ] `save_progress` 首次 INSERT、再次 UPDATE（同一 `book_id` 单行、`updated_at` 推进、`read_count` 不被写回重置）；`increment_read_count` 0→1→2、无进度行时建默认行 `read_count=1`、`nyx_position` 落 `total`
  - [ ] `list_books` 未读书 `user_position=0`/`last_read_at=None`、读过的书 `last_read_at` 非空且排前
  - [ ] `list_paragraphs` 范围查询：`from=2&to=4` 只回 `index 2..4`、升序、`is_chapter_start` bool 还原（INTEGER 1 → `True`）
  - [ ] 删 book → `reading_progress` CASCADE 清空
  - [ ] `get_progress`/`save_progress`/`list_paragraphs` 对不存在书抛 `BookNotFoundError`；`get_progress` 无进度行返回默认（`user_position=1`、`updated_at=0.0`）；`list_paragraphs` `to > total_paragraphs` 抛 `ValueError`
- [ ] 契约测试 `tests/test_api/test_reading_api.py`（fake `ReadingFacade`）：
  - [ ] `GET /api/books` 返回 `[BookListItem]` 且排序正确
  - [ ] `GET /api/progress/{book_id}` 无记录 → 默认进度；有记录 → 落库值；书不存在 → 404
  - [ ] `PUT /api/progress/{book_id}` 缺 `reading_speed` → 422；`reading_speed` 超界（9 或 201）→ 422；书不存在 → 404
  - [ ] `GET /api/books/{book_id}/paragraphs` 书不存在 → 404；缺 `from`/`to` → 422；`from<1` 或 `to<from` → 422；`to > total_paragraphs` → 422
- [ ] E2E：开书 → 翻页 → 关页 → 重开恢复位置（手动，阶段 3）

## 完成定义

- [ ] `ruff check` 零报错
- [ ] `pyright` 零报错
- [ ] `pytest` 全绿
- [ ] `test-inventory.md` 已更新（快照）
- [ ] ripple 同步：tech-ref §2 实体计数 +2（`ReadingProgress` 加 `read_count` 字段/`BookListItem`）、§3 业务表计数 +1（`reading_progress` 加 `read_count` 列）、§5 补 `ReadingFacade` 清单（`list_books`/`list_paragraphs`/`get_progress`/`save_progress`）、§7 包结构补 `reading/store.py` 进度方法（含 `increment_read_count`）/`facade.py`、§4 REST 表补 4 个 reading 端点、01-types 实体计数 +2
- [ ] 用户能从书架选书、翻页、下次打开恢复进度
