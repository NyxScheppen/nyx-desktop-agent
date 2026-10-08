# 阅读事件（读书反应 + 笔记/划线/书签）

> 前端「陪伴读书」的**行为与标记层**：订阅 `reading_mutter`/`reading_question`/`reading_association`/`reading_progress` 四个 SSE 事件——读书碎碎念归悬浮气泡、提问/联想并进对话、进度快照同步到阅读页；阅读页提供段内划线、当前书划线搜索、书签和持久定位，笔记面板继续负责普通用户笔记 CRUD + 「给尼克斯看」批注。Nyx 的章末整合记忆不在此上屏（落 memory）。
> 范围：`components/reading/{ReaderView,NotePanel}.tsx` + `components/chat/MessageBubble.tsx` + `stores/readerStore.ts` + `api/client.ts` + `api/dispatch.ts` + `hooks/useSSE.ts`。
> 对齐后端：`12-reading-system`（4 个 `READING_*` 事件、笔记 CRUD/批注/章末整合端点）。
> 反向修订阅读系统：`show-to-nyx` 端点返回体从 `{annotation_id, content}` 改为完整 `Annotation`（`{id, user_note_id, content, created_at}`）——前端 append 完整对象，不造 `created_at`。

## 1. 新 SSE 事件（后端 12-reading-system 定义，前端增补四型）

后端 12-reading-system 在 `EventType` 追加 `READING_MUTTER`/`READING_QUESTION`/`READING_ASSOCIATION`/`READING_PROGRESS`，经既有 `GET /api/events` 广播。`data` 形状（01-sse §1 约定：`{event_id, correlation_id, timestamp} + content`；`correlation_id` = `book_id`，按书归组）：

```
event: reading_mutter
data: {"event_id":"…","correlation_id":"<book_id>","content":"…","book_id":"…","paragraph_index":12}
```

| 事件（`e.event`） | `content` 键（除 `event_id`/`correlation_id`） | 说明 |
|---|---|---|
| `reading_mutter` | `{content, book_id, paragraph_index}` | 读到精彩处碎碎念 |
| `reading_question` | `{content, subtype, book_id, paragraph_index, selected_text}` | 冲动提问；`subtype` = 四子型之一；`selected_text` 仅 `quote_question` 非空 |
| `reading_association` | `{memory_id, snippet, book_id, paragraph_index}` | 记忆联想（每个命中记忆一条） |

### TS 类型（`types/api.ts` 增补）

```typescript
type QuestionSubtype = "question_knowledge" | "question_personal" | "question_reflective" | "quote_question";
type ReadingMutterEvent = SseBase & {
  event: "reading_mutter";
  content: string;
  book_id: string;
  paragraph_index: number;
};
type ReadingQuestionEvent = SseBase & {
  event: "reading_question";
  content: string;
  subtype: QuestionSubtype;                 // question_knowledge | question_personal | question_reflective | quote_question
  book_id: string;
  paragraph_index: number;
  selected_text: string | null;
};
type ReadingAssociationEvent = SseBase & {
  event: "reading_association";
  memory_id: string;
  snippet: string;                 // summary or content 截断 ~80 字
  book_id: string;
  paragraph_index: number;
};
```

- 四型并入 `SseEvent` 判别联合；`hooks/useSSE.ts` 的 `EVENT_TYPES` 数组同步加四值（01-sse §4 前向兼容边界：新增 EventType 必须同步 `EVENT_TYPES` + 判别联合 + 分发表）。

### 阅读进度快照事件

`reading_progress` 是后台阅读、其它窗口或重连回放使用的第四个阅读事件，不进入聊天或
阅读反应 buffer。帧字段为：

```typescript
type ReadingProgressEvent = SseBase & {
  event: "reading_progress";
  book_id: string;
  user_position: number;
  nyx_position: number;
  reading_speed: number;
  read_count: number;
  revision: number;
};
```

`dispatchEvent` 路由到 `readerStore.applyProgressEvent`。store 只应用当前书、严格高于本地
`progressRevision` 且 `nyx_position` 不回退的快照；异书、重复/旧 revision 和 Nyx 回退直接
丢弃。应用后按 Nyx 是否落后控制追赶，不覆盖本地未提交的 `userPosition`。

## 2. 读书反应：并进对话 / 悬浮气泡（08 §2/§3）

- **分派**（`api/dispatch.ts` 重路由，三 case 不再进 `readerStore`）：

```typescript
case "reading_mutter":
  return announceStore.announce("mutter", e.content);   // 读书碎碎念归悬浮气泡
case "reading_question":
case "reading_association":
  return chatStore.addReadingTurn(e);                   // 读书提问/联想并进对话
```

- **`chatStore.addReadingTurn`（并进对话）**：`e: ReadingQuestionEvent | ReadingAssociationEvent`。

```typescript
// question → { kind:"reading_question", content:e.content, subtype:e.subtype, selectedText:e.selected_text, correlation_id:e.book_id }
// association → { kind:"reading_association", content:e.snippet, memoryId:e.memory_id, correlation_id:e.book_id }
```

  - `correlation_id = e.book_id`（后端用 `book_id` 当 correlation_id，前端照填）；**不过滤当前书**——读书 turn 是永久聊天消息（同 `initiate_chat`），关书后转录仍留。
  - 复用 `append` 的「文本字段非 string 丢弃」收窄（question 验 `content`、association 验 `snippet`）。
- **渲染契约（`MessageBubble`）**：读书 turn **不进** `isNyxText`/`NYX_TEXT_KINDS` 白名单 → 即时全量、不进打字机串行门。`reading_question` →「提问」徽标 + `selectedText` 非空渲染「原文：{selectedText}」引文行；`reading_association` →「联想」徽标 + `memoryId` 存在渲染「记忆」标。
- **`reading_mutter` 归悬浮气泡**：走 `announceStore.announce("mutter", e.content)`，与全局 `mutter`/`reflection_done` 同一渲染路径（瞬时气泡几秒淡出，不落聊天历史）。

## 3. 笔记、划线与书签

### 组件树

```
NotePanel                # 从 reader__footer「笔记」入口打开（覆盖层/主区切换）
├─ NoteList              # GET /api/notes/{book_id} 列表，created_at DESC
│  └─ NoteItem           # 单条：content + selected_text 引用 + 批注列表 + 「给尼克斯看」/ 编辑 / 删除
└─ NoteComposer          # 新建普通自由文本笔记；纯划线不在此列表显示
ReaderView
├─ SelectionToolbar      # 同一 Paragraph 内选区 → 保存纯划线
├─ HighlightModal        # 当前书划线列表 + selected_text 大小写不敏感过滤 + 定位/删除
└─ BookmarkModal         # 当前书书签列表 + 定位/删除
```

### 共享类型（`types/api.ts` 增补）

```typescript
type Annotation = { id: string; user_note_id: string; content: string; created_at: number };
type UserNote = {
  id: string;
  book_id: string | null;          // 书删后 SET NULL
  paragraph_id: string | null;     // 段落删后 SET NULL
  content: string;
  selected_text: string | null;
  paragraph_index?: number | null; // 列表查询由段落关联派生
  selection_start?: number | null; // UTF-16 code unit 闭开区间
  selection_end?: number | null;
  created_at: number;
  updated_at: number;
};
type UserNoteWithAnnotations = UserNote & { annotations: Annotation[] };  // GET /api/notes/{book_id} 每条附带（created_at DESC）
type Bookmark = {
  id: string;
  book_id: string;
  paragraph_id: string;
  paragraph_index: number;
  preview: string;
  created_at: number;
};
```

### readerStore 笔记 state/actions

```typescript
notes: UserNoteWithAnnotations[];                 // 当前书用户笔记（含批注）
notesError: string | null;
bookmarks: Bookmark[];
bookmarksError: string | null;

loadNotes(): Promise<void>                  // GET /api/notes/{bookId} → notes
addNote(p: {book_id, paragraph_id?, content, selected_text?, selection_start?, selection_end?}): Promise<void>
updateNote(id: string, content: string): Promise<void>  // PUT → 返回裸 UserNote → 覆盖字段（保留 annotations 数组）
deleteNote(id: string): Promise<void>                // DELETE → 本地移除该条（连同其批注）
showToNyx(noteId: string): Promise<void>             // POST show-to-nyx → 返回完整 Annotation → append 到该 note.annotations
loadBookmarks(): Promise<void>
toggleBookmark(paragraphId: string): Promise<void>
```

### 关键决策

- **普通笔记与纯划线共表但分入口**：纯划线使用 `content=""` 和完整选区四元组，`NotePanel` 只展示 `content` 非空的普通笔记；历史无 offset 的引用仍可显示为普通笔记，但不绘制正文高亮。
- **只允许单段选区**：`ReaderView` 要求 anchor/focus 属于同一 `data-paragraph-id`，offset 直接沿用浏览器/JS 的 UTF-16 code unit；跨段或空白选区不显示保存操作。
- **查询与定位**：划线搜索只过滤当前书已加载 notes 的有效 `selected_text`，不请求 LLM 或新服务端端点；点击划线或书签统一调用 `jumpToPosition`，保存进度但不补发跨段冲动。
- **只展示用户笔记 + 批注**：Nyx 章末整合的笔记走 `remember_reading` 落 memory（`kind='reading'`），不上屏；`showToNyx` 的批注挂在用户笔记的 `annotations` 下。
- **「给尼克斯看」主动触发**：Nyx 不主动读用户笔记（12-reading-system 决策 C3）；`showToNyx` 读笔记 + 原段落 → LLM 批注 → 插 `annotations`。多次展示 → 每次新增一行批注（不覆盖）。
- **章末检测由追赶循环触发**：06 的 `advanceNyx` 每次 `nyxPosition += 1` 后 fire-and-forget `checkChapterBoundary(bookId, nyxPosition)`；`is_boundary=true` 时后端后台整合（落 memory，不阻塞返回）。前端不渲染结果（见上条）。
- **`showToNyx` 本地 append 批注**：成功后把返回的完整 `Annotation`（12-reading-system 的 `show-to-nyx` 回 `{id, user_note_id, content, created_at}`，非 `{annotation_id, content}`）追加到该 note 的 `annotations`，不整表重拉（避免用户翻笔记时抖动）；失败静默记 `notesError`。

## 4. `client.ts` 增补（笔记端点）

```typescript
async function getNotes(bookId: string): Promise<UserNoteWithAnnotations[]>                                   // GET /api/notes/{bookId}
async function createUserNote(p: { book_id: string; paragraph_id?: string | null; content: string; selected_text?: string | null; selection_start?: number | null; selection_end?: number | null }): Promise<UserNote>
async function updateUserNote(id: string, content: string): Promise<UserNote>                  // PUT /api/notes/user/{id}
async function deleteUserNote(id: string): Promise<void>                                       // DELETE /api/notes/user/{id}
async function showNoteToNyx(noteId: string): Promise<Annotation | null>  // POST /api/notes/{noteId}/show-to-nyx（返回完整 Annotation；LLM 空/失败回 null）
async function checkChapterBoundary(bookId: string, nyxPosition: number): Promise<{ is_boundary: boolean; book_finished: boolean }>  // POST /api/notes/check-chapter-boundary
async function getBookmarks(bookId: string): Promise<Bookmark[]>                            // GET /api/bookmarks/{bookId}
async function createBookmark(bookId: string, paragraphId: string): Promise<Bookmark>       // POST /api/bookmarks
async function deleteBookmark(id: string): Promise<void>                                    // DELETE /api/bookmarks/{id}
```

- 请求体键 = 后端键（snake_case 零映射）；`createUserNote` 缺 `content` → 422（客户端上抛）、`updateUserNote`/`deleteUserNote`/`showNoteToNyx` 不存在 → 404 上抛（统一错误契约，05-client §2）。
- `checkChapterBoundary` 由 06 追赶循环调用（非面板按钮），故放本 spec 一并定义（与 12-reading-system 端点 1:1）。

## 5. 对既有前端 spec 的修订

- `01-sse.md`：§2 `SseEvent` 判别联合 + §4 分发表 + `EVENT_TYPES` 数组各增 `reading_mutter`/`reading_question`/`reading_association` 三型（`reading_mutter` → `announceStore`、`reading_question`/`reading_association` → `chatStore.addReadingTurn`）。
- `05-client.md`：§1 端点列表同步阅读、笔记和书签函数（06 §6 + 本 spec §4）。
- `02-stores.md`：增 `readerStore` 条目（state 形状 + actions 完整实现，本文档只给签名）。

## 6. 测试（`tests/` 并入 api/sse/stores 测试）

- `useSSE`/分派（`tests/sse.test.ts` 增补）：mock `EventSource` 派发三型帧 → `dispatch` 路由 `reading_question`/`reading_association` 到 `chatStore.addReadingTurn`、`reading_mutter` 到 `announceStore.announce("mutter")`；`EVENT_TYPES` 含三值（缺则帧被静默丢弃，01-sse §4）。
- `chatStore.addReadingTurn`（`tests/stores.test.ts` 增补）：question → `kind==="reading_question"` + `subtype`/`selectedText` 落对、association → `kind==="reading_association"` + `memoryId`/`content=e.snippet` 落对；两条都 `correlation_id===book_id`；文本字段非 string 丢弃。
- `readerStore` 笔记：`loadNotes` 落 `notes`；`addNote` unshift 归一 `annotations: []`；`updateNote` 覆盖返回字段并保留 annotations；`deleteNote` 移除；`showToNyx` 成功后 `annotations` append 返回的完整 `Annotation`（不整表重拉）。
- `readerStore` 书签：加载、添加、取消更新当前书列表；`jumpToPosition` 保存位置、重拉窗口且不请求冲动端点。
- `client`（`tests/api.test.ts` 增补）：笔记选区 offset 与三个书签函数的端点/方法/请求体键。
- 组件（`tests/notePanel.test.tsx`）：NotePanel 渲染 content/selected_text/批注、composer 提交 `addNote`、空白禁用、「给尼克斯看」/「删除」/「编辑」按钮 wiring（编辑态保存 → `updateNote`（trim）、取消退出不调、空白保存禁用）。
- `ReaderView`（`tests/readerView.test.tsx`）：结构化标题/加粗/已有划线、划线过滤与持久定位、同段跨格式选区的 UTF-16 offset、跨段拒绝、书签列表定位。
- 不依赖真实后端；验证管道正确（事件走对 store、进度快照过滤、笔记 CRUD 对），不验证视觉。
