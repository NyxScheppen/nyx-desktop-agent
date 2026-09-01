# 阅读面板（书架 + 阅读页 + `readerStore` + Nyx 追赶）

> 前端「陪伴读书」的**内容与进度层**：shell 加「读书」入口 → 书架 → 阅读页（左正文、右 Nyx 侧栏）。用户翻页、Nyx 按 `reading_speed` 逐段追赶（前端 `setTimeout`，秒级逐段）、进度持久化、翻页触发冲动评估。
> 范围：`components/reading/{BookshelfView,ReaderView,ReaderSidebar}.tsx` + `stores/readerStore.ts` + `api/client.ts` 阅读端点。冲动气泡流与笔记面板见 `07-reading-events.md`。
> 对齐后端：`19-reading-content`（books/paragraphs）、`20-reading-progress`（书架/进度/分页）、`21-reading-impulse`（`POST /api/impulse/evaluate`）。

## 1. 组件树

```
（shell 底部导航加「读书」入口，见 §5 装配）
BookshelfView            # 书架：GET /api/books 列表 + 「导入 EPUB」按钮；点书 → openBook 进阅读页
ReaderView               # 阅读页：左正文段落流 + 右 ReaderSidebar；翻页 + 进度恢复 + 翻页触发冲动评估
├─ ReaderText            # 当前窗口段落（GET /api/books/{id}/paragraphs?from&to），is_chapter_start 段渲染章首分隔
└─ ReaderSidebar         # Nyx 侧栏：她读到第几段（nyx_position 追赶）+ 冲动气泡流（07）+ 笔记入口（07）
NotePanel                # 笔记面板（07 定义；06 只在 ReaderSidebar 留「笔记」入口）
```

## 2. 共享类型（`types/api.ts` 增补）

```typescript
type Book = {
  id: string;
  title: string;
  author: string;
  filename: string;
  content_hash: string;
  total_paragraphs: number;
  created_at: number;
  updated_at: number;
};
type BookListItem = {
  id: string;
  title: string;
  author: string;
  filename: string;
  total_paragraphs: number;
  user_position: number;         // 读到第几段；未读 0
  last_read_at: number | null;   // 未读 null（书架排序键）
};
type Paragraph = {
  id: string;
  book_id: string;
  index: number;                 // 1-based
  text: string;
  is_chapter_start: boolean;     // 章首段（章末检测/章首分隔渲染用）
};
type Progress = {                 // GET /api/progress/{id} 返回（读，4 键）
  user_position: number;
  nyx_position: number;
  reading_speed: number;         // 字符/秒（10–200，默认 50）
  read_count: number;            // 读完几遍（0=未读完，>=1 可重读）
};
type ProgressInput = {            // PUT /api/progress/{id} 请求体（写，3 键，不含 read_count）
  user_position: number;
  nyx_position: number;
  reading_speed: number;
};
```

> 字段名 = 后端 JSON 键（snake_case 零映射，README §4）。`Progress` 无记录时后端返回默认 `{user_position:1, nyx_position:1, reading_speed:50, read_count:0}`。

## 3. `readerStore`

### state

```typescript
type NyxStatus = "idle" | "reading" | "waiting";  // 派生态，不落 store

type ReaderState = {
  books: BookListItem[];          // 书架快照
  booksError: string | null;
  bookId: string | null;          // 当前打开的书（null = 未开书）
  totalParagraphs: number;        // 当前书总段数（openBook 从 books 列表项取；0 = 未开书）
  paragraphs: Paragraph[];        // 当前窗口段落（分页，§5 窗口规则）
  windowFrom: number;             // 当前窗口起始 index（1-based）
  userPosition: number;           // 用户读到第几段（1-based）
  nyxPosition: number;            // Nyx 读到第几段（1-based）
  readingSpeed: number;           // 字符/秒
  readCount: number;              // 读完几遍（0=未读完，>=1 可重读）
  // 冲动气泡 + 笔记见 07，同属本 store（阅读系统唯一 store）
};
```

### actions

```typescript
loadBooks(): Promise<void>                      // GET /api/books → books；失败 → booksError
openBook(bookId: string): Promise<void>         // totalParagraphs = books.find(b=>b.id===bookId)?.total_paragraphs ?? 0 → getProgress + 拉首窗口段落 → 会话态；nyx < user → startCatchup()
closeBook(): void                               // stopCatchup() + bookId/paragraphs/positions 复位
syncPosition(next: number): Promise<void>        // 位置同步（滚到哪段同步到哪）：clamp [1, total] → 存进度 + 前翻逐段补发 evaluateImpulse + 必要时翻窗口
setReadingSpeed(speed: number): Promise<void>   // putProgress({user_position, nyx_position, reading_speed: speed})（三键全量；已排 timer 按旧 speed 走完当前段，新 speed 下一段才生效）
startCatchup(): void                            // 起 setTimeout 追赶循环（§4）
stopCatchup(): void                             // clearTimeout 停追赶
advanceNyx(): void                              //（内部）nyxPosition += 1 → checkChapterBoundary(07) + 续排下段
reread(): Promise<void>                          // 重读：putProgress({user_position:1, nyx_position:1, reading_speed}) 复位进度；read_count 后端不碰（保持 >=1）
```

> 追赶 timer 放 module-level（`let catchupTimer`，不进 store state，同 `chatStore` 的 `replyTimer`/`pendingId` 约定，见 02-stores §1）。

### 关键决策

- **`nyxStatus` 是派生态**：`idle`（`bookId===null`）/ `reading`（`nyxPosition < userPosition`）/ `waiting`（`nyxPosition >= userPosition`）——与后端 spec 20 决策一致（后端不存 `nyx_status`，前端派生）。
- **阅读系统一个 store**：书架/进度/段落/冲动气泡/笔记同属「陪伴读书」一个系统，归 `readerStore`（CLAUDE.md「每系统一个 store」）。不拆 `noteStore`/`impulseStore`（反冗余）。
- **进度持久化后写**：位置同步 `syncPosition` 每次 `putProgress(userPosition, nyxPosition, readingSpeed)`（fire-and-forget，失败静默、下次翻页重写覆盖）；`nyxPosition` 由追赶循环推进，也随下次 `putProgress` 落库（后端已定「state 是 value 派生」不存派生态，前端把最新 nyx 位置随进度写回即可）。
- **整屏翻 + 高亮定位**：正文是滚动容器，`ReaderView` 的「上一页/下一页」滚一整屏（`scrollBy(clientHeight)`）；`onScroll` 把「页顶段」同步回 `syncPosition`，当前段 `--current` 高亮、Nyx 段 `--nyx` 🦊 标记（滚轮与按钮同一条同步路径，计数/高亮不漂移）。
- **正文后端唯一来源**：`evaluateImpulse` 只传 `{book_id, paragraph_index, last_paragraph_index}`（不传 `paragraph_text`），正文后端自取（21 决策）。
- **「读完」「重读」是前端动作**：`userPosition == total_paragraphs` 时显示「读完」（UI 确认，无后端调用）；`Progress.read_count >= 1` 时显示「重读」（`reread()` = `putProgress({user_position:1, nyx_position:1, reading_speed})` 复位）。后端「读完」标记是 `read_count`（22 的整本读完自动 `++`），前端不额外写 finished；重读触发反思全在后端 22，前端只需复位进度。
- **`totalParagraphs` 来自书架列表项**：后端 `GET /api/progress` 不回 total、段落窗口只回窗口内段，故唯一现成来源是 `BookListItem.total_paragraphs`；`openBook` 用 `books.find(b => b.id === bookId)` 落 `readerStore.totalParagraphs`。**前置 `books` 已加载**（书架点书天然已 `loadBooks`；深链/刷新先 `loadBooks` 再 `openBook`），否则 `totalParagraphs=0`、clamp 失效——`syncPosition` 需 `totalParagraphs>0` 守卫，0 时不推进。

## 4. Nyx 追赶（`setTimeout`，秒级逐段）

> 设计文档 §5.4：逐段追赶是**秒级**，后端不另起 tick 推进 Nyx，前端 `setTimeout` 按 `reading_speed` 推进 `nyxPosition`。

- **节奏**：Nyx 每读一段耗时 `duration = clamp(段落字数 / readingSpeed, 1, 30)` 秒（`readingSpeed` 字符/秒，段越长读越久；`1`/`30` 为保底上下界，decision 可推翻）。
- **循环**：`startCatchup()` 时若 `nyxPosition >= userPosition` 直接返回（无需追）。否则对「Nyx 当前即将读的段」（`paragraphs` 里 `index === nyxPosition` 的段，取 `text.length`）算 `duration` → `catchupTimer = setTimeout(advanceNyx, duration*1000)`；`advanceNyx` 里 `nyxPosition += 1` 后 `checkChapterBoundary(bookId, nyxPosition)`（fire-and-forget，07）→ 若仍 `nyxPosition < userPosition` 续排下一段，否则停止（`waiting`）。
- **触发/停止**：`openBook`（恢复后 nyx 落后）与 `syncPosition`（前翻拉开差距）调 `startCatchup`；`closeBook`/`stopCatchup` `clearTimeout`；段落窗口里找不到 `nyxPosition` 段（未加载）时按 `MIN_CATCHUP_SEC=1` 保底节奏，不阻塞。
- **重入安全**：`startCatchup` 先 `clearTimeout` 旧 timer 再排新（避免连点叠多个 timer）；`advanceNyx` 里 `nyxPosition` 上限 `userPosition`（不超车）。

## 5. 翻页流程与装配

```
书架点书 → openBook(bookId)
  → getProgress + getBookParagraphs(bookId, user_position, user_position+WINDOW-1)（首窗口对齐用户位置）
  → nyxPosition < userPosition ? startCatchup() : waiting

整屏翻 → 按钮/滚轮滚动 .reader-text 一整屏（scrollBy clientHeight）
  → onScroll（rAF 节流）算「页顶段」idx = 最后一个 offsetTop <= scrollTop+8 的段
  → syncPosition(idx)
      → userPosition = clamp(idx, 1, total_paragraphs)
      → putProgress(...)（异步写回，fire-and-forget）
      → 前翻（idx > 旧值）逐段补发 evaluateImpulse(bookId, i, i-1)，i ∈ (旧, idx]（整屏翻一次跨 N 段，逐段保住每段都有机会触发；气泡走 SSE 07）
      → userPosition 越窗口 80% 边界时重拉新窗（centered=false，从 userPosition 起）
      → startCatchup()（拉开差距 Nyx 继续追）
  → 正文里高亮当前段（--current）+ 🦊 标 Nyx 段（--nyx）
```

- **窗口规则**：`WINDOW_SIZE = 50`（每窗段数，decision 可推翻）。`openBook` 拉 `[user_position, user_position+WINDOW_SIZE-1]`；`syncPosition` 到窗口边界（`userPosition` 超出 `[windowFrom, windowFrom+WINDOW_SIZE-1]` 的 80%）时重拉**从 `userPosition` 起**的新窗（`centered=false`，当前段恒为窗口顶）。**请求前 clamp 到 `[1, total_paragraphs]`**（`from=max(1, …)`、`to=min(total_paragraphs, …)`）——后端 20 对 `from<1`/`to>total` 返回 422（越界不截断），前端必须先 clamp，否则书首/书尾窗口会 422。`nyxPosition` 追赶只在窗口内段有字长可算（§4 兜底）。
- **翻页方向守卫**：`evaluateImpulse` 后端的 `paragraph_index <= last_paragraph_index → []` 已兜底回翻不触发；前端只在前翻时调用（回翻不评估），双保险。
- **装配**：shell 底部导航加「读书」入口按钮 → 打开 `BookshelfView`（覆盖层/主区切换）；`ReaderView` 打开时 `ReaderSidebar` 读 `readerStore`（`nyxPosition`/`userPosition` 派生的追赶进度条 + 07 的气泡/笔记）。`useSSE` 不在此（挂 App 层，01-sse §4），阅读事件经 `dispatch.ts` 进 `readerStore`（07）。

## 6. `client.ts` 增补

```typescript
async function getBooks(): Promise<BookListItem[]>                                          // GET /api/books
async function getBookParagraphs(bookId: string, from: number, to: number): Promise<Paragraph[]>  // GET /api/books/{id}/paragraphs?from=&to=
async function getProgress(bookId: string): Promise<Progress>                               // GET /api/progress/{id}
async function putProgress(bookId: string, p: ProgressInput): Promise<void>                  // PUT /api/progress/{id}（body 3 键，不含 read_count）
async function importBook(file: File): Promise<Book>                                // POST /api/books（multipart，Content-Type 不设 json，用 FormData）
async function evaluateImpulse(bookId: string, paragraphIndex: number, lastParagraphIndex: number): Promise<{ triggered: string[] }>  // POST /api/impulse/evaluate
```

- `importBook` 是唯一 multipart 端点：`FormData` + `file` 字段，**不设** `Content-Type: application/json`（浏览器自动带 boundary）；成功 201 返回新书 → 前端 `loadBooks()` 刷新书架。
- 错误契约沿用 05-client §2：非 2xx 读 `detail` 后 throw；`importBook` 409 重复（body 含 `existing_book_id`/`title`）、400 非 epub/超限、500 解析失败都走统一 throw。

## 7. 测试（`tests/` 并入 api/stores 测试）

- `client`（`tests/api.test.ts` 增补）：`getBooks`/`getBookParagraphs`（`from`/`to` 拼进 query）/`getProgress`/`putProgress`（PUT + body 键 `{user_position, nyx_position, reading_speed}`）/`importBook`（FormData、不设 json 头）/`evaluateImpulse` 各断言端点与方法；非 2xx 统一 throw。
- `readerStore`（`tests/stores.test.ts` 增补）：`loadBooks` 落 `books`；`openBook` mock `getProgress`+`getBookParagraphs` → 会话态 + `totalParagraphs` 正确（从 books 列表项取）、`nyx<user` 时 `startCatchup` 被调；`syncPosition` 前翻跨越 N 段 → 逐段 `evaluateImpulse(bookId, i, i-1)` 被调 + `putProgress` 一次、回翻/同段 → 不评估；`nyxStatus` 派生三态正确；书首/书尾（`from`/`to` 越界）→ `getBookParagraphs` 请求 clamp 到 `[1, total_paragraphs]` 不越界；`reread` → `putProgress({user_position:1, nyx_position:1, reading_speed})` 复位、`userPosition=nyxPosition=1`。
- **追赶循环**（fake timers）：`openBook` 后 `nyxPosition < userPosition` → `advanceTimersByTime(duration)` 推进 `nyxPosition += 1` 且续排下段；`nyxPosition` 到 `userPosition` → 停止（不再排 timer）；`closeBook`/`stopCatchup` → `clearTimeout`（再 `advanceTimersByTime` 不推进）；`startCatchup` 重入 → 旧 timer 清除不叠加。
- 不依赖真实后端；验证管道正确（端点走对、追赶循环时序对、派生态对），不验证视觉。
