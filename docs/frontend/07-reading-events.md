# 阅读事件（SSE 冲动气泡 + 笔记面板）

> 前端「陪伴读书」的**行为与笔记层**：订阅 `reading_mutter`/`reading_question`/`reading_association` 三个 SSE 事件——读书碎碎念归悬浮气泡、提问/联想并进对话；笔记面板做用户笔记 CRUD + 「给尼克斯看」批注。Nyx 的章末整合记忆不在此上屏（落 memory，记忆面板已砍）。
> 范围：`components/reading/NotePanel.tsx` 笔记部分 + `components/chat/MessageBubble.tsx` 读书 turn 渲染 + `stores/readerStore.ts` 笔记 state + `api/client.ts` 笔记端点 + `api/dispatch.ts` 阅读事件分派 + `hooks/useSSE.ts` 的 `EVENT_TYPES`。
> 对齐后端：`21-reading-impulse`（3 个 `READING_*` 事件）、`22-reading-notes`（笔记 CRUD/批注/章末整合端点）。
> 反向修订 22：`show-to-nyx` 端点返回体从 `{annotation_id, content}` 改为完整 `Annotation`（`{id, user_note_id, content, created_at}`）——前端 append 完整对象，不造 `created_at`。

## 1. 新 SSE 事件（后端 21 定义，前端增补三型）

后端 21 在 `EventType` 追加 `READING_MUTTER`/`READING_QUESTION`/`READING_ASSOCIATION`，经既有 `GET /api/events` 广播。`data` 形状（01-sse §1 约定：`{event_id, correlation_id} + content`；`correlation_id` = `book_id`，21 决策「按书归组」）：

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

- 三型并入 `SseEvent` 判别联合；`hooks/useSSE.ts` 的 `EVENT_TYPES` 数组同步加三值（01-sse §4 前向兼容边界：新增 EventType 必须同步 `EVENT_TYPES` + 判别联合 + 分发表）。

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

## 3. 笔记面板（`NotePanel`）

### 组件树

```
NotePanel                # 从 reader__footer「笔记」入口打开（覆盖层/主区切换）
├─ NoteList              # GET /api/notes/{book_id} 列表，created_at DESC
│  └─ NoteItem           # 单条：content + selected_text 引用 + 批注列表 + 「给尼克斯看」/ 编辑 / 删除
└─ NoteComposer          # 新建笔记：自由文本；从正文选中文本点「记笔记」时预填 selected_text + paragraph_id
```

### 共享类型（`types/api.ts` 增补）

```typescript
type Annotation = { id: string; user_note_id: string; content: string; created_at: number };
type UserNote = {                   // 裸 7 键，对齐后端 TypedDict（POST/PUT 返回，无 annotations）
  id: string;
  book_id: string | null;          // 书删后 SET NULL
  paragraph_id: string | null;     // 段落删后 SET NULL
  content: string;
  selected_text: string | null;
  created_at: number;
  updated_at: number;
};
type UserNoteWithAnnotations = UserNote & { annotations: Annotation[] };  // GET /api/notes/{book_id} 每条附带（created_at DESC）
```

### readerStore 笔记 state/actions

```typescript
notes: UserNoteWithAnnotations[];                 // 当前书用户笔记（含批注）
notesError: string | null;

loadNotes(): Promise<void>                  // GET /api/notes/{bookId} → notes
addNote(p: {book_id, paragraph_id?, content, selected_text?}): Promise<void>  // POST → 返回裸 UserNote → 归一 {…note, annotations: []} unshift
updateNote(id: string, content: string): Promise<void>  // PUT → 返回裸 UserNote → 覆盖该条 7 键（保留 annotations 数组）
deleteNote(id: string): Promise<void>                // DELETE → 本地移除该条（连同其批注）
showToNyx(noteId: string): Promise<void>             // POST show-to-nyx → 返回完整 Annotation → append 到该 note.annotations
```

### 关键决策

- **只展示用户笔记 + 批注**：Nyx 章末整合的笔记走 `remember_reading` 落 memory（`tag='reading'`），**不上屏**（记忆面板已砍，README §5）；`NotePanel` 是「用户笔记」面板，用户笔记与 Nyx 笔记严格分离（22 决策）。`showToNyx` 的批注是「对用户笔记的回应」，挂在 `annotations` 下，与 Nyx 自己落 memory 的笔记无关。
- **「给尼克斯看」主动触发**：Nyx 不主动读用户笔记（22 决策 C3）；`showToNyx` 读笔记 + 原段落 → LLM 批注 → 插 `annotations`。多次展示 → 每次新增一行批注（不覆盖）。
- **章末检测由追赶循环触发**：06 的 `advanceNyx` 每次 `nyxPosition += 1` 后 fire-and-forget `checkChapterBoundary(bookId, nyxPosition)`；`is_boundary=true` 时后端后台整合（落 memory，不阻塞返回）。前端不渲染结果（见上条）。
- **`showToNyx` 本地 append 批注**：成功后把返回的完整 `Annotation`（22 的 `show-to-nyx` 回 `{id, user_note_id, content, created_at}`，非 `{annotation_id, content}`）追加到该 note 的 `annotations`，不整表重拉（避免用户翻笔记时抖动）；失败静默记 `notesError`。

## 4. `client.ts` 增补（笔记端点）

```typescript
async function getNotes(bookId: string): Promise<UserNoteWithAnnotations[]>                                   // GET /api/notes/{bookId}
async function createUserNote(p: { book_id: string; paragraph_id?: string | null; content: string; selected_text?: string | null }): Promise<UserNote>  // POST /api/notes/user
async function updateUserNote(id: string, content: string): Promise<UserNote>                  // PUT /api/notes/user/{id}
async function deleteUserNote(id: string): Promise<void>                                       // DELETE /api/notes/user/{id}
async function showNoteToNyx(noteId: string): Promise<Annotation | null>  // POST /api/notes/{noteId}/show-to-nyx（返回完整 Annotation；LLM 空/失败回 null）
async function checkChapterBoundary(bookId: string, nyxPosition: number): Promise<{ is_boundary: boolean; book_finished: boolean }>  // POST /api/notes/check-chapter-boundary
```

- 请求体键 = 后端键（snake_case 零映射）；`createUserNote` 缺 `content` → 422（客户端上抛）、`updateUserNote`/`deleteUserNote`/`showNoteToNyx` 不存在 → 404 上抛（统一错误契约，05-client §2）。
- `checkChapterBoundary` 由 06 追赶循环调用（非面板按钮），故放本 spec 一并定义（与 22 端点 1:1）。

## 5. 对既有前端 spec 的修订

- `01-sse.md`：§2 `SseEvent` 判别联合 + §4 分发表 + `EVENT_TYPES` 数组各增 `reading_mutter`/`reading_question`/`reading_association` 三型（`reading_mutter` → `announceStore`、`reading_question`/`reading_association` → `chatStore.addReadingTurn`）。
- `05-client.md`：§1 端点列表增 6 个阅读 + 6 个笔记函数（06 §6 + 本 spec §4）。
- `02-stores.md`：增 `readerStore` 条目（state 形状 + actions 完整实现，本文档只给签名）。

## 6. 测试（`tests/` 并入 api/sse/stores 测试）

- `useSSE`/分派（`tests/sse.test.ts` 增补）：mock `EventSource` 派发三型帧 → `dispatch` 路由 `reading_question`/`reading_association` 到 `chatStore.addReadingTurn`、`reading_mutter` 到 `announceStore.announce("mutter")`；`EVENT_TYPES` 含三值（缺则帧被静默丢弃，01-sse §4）。
- `chatStore.addReadingTurn`（`tests/stores.test.ts` 增补）：question → `kind==="reading_question"` + `subtype`/`selectedText` 落对、association → `kind==="reading_association"` + `memoryId`/`content=e.snippet` 落对；两条都 `correlation_id===book_id`；文本字段非 string 丢弃。
- `readerStore` 笔记：`loadNotes` 落 `notes`；`addNote` unshift 归一 `annotations: []`；`updateNote` 覆盖 7 键保留 annotations；`deleteNote` 移除；`showToNyx` 成功后 `annotations` append 返回的完整 `Annotation`（不整表重拉）。
- `client`（`tests/api.test.ts` 增补）：6 个笔记函数端点/方法/请求体键 + 非 2xx 统一 throw（422/404）。
- 组件（`tests/notePanel.test.tsx`）：NotePanel 渲染 content/selected_text/批注、composer 提交 `addNote`、空白禁用、「给尼克斯看」/「删除」/「编辑」按钮 wiring（编辑态保存 → `updateNote`（trim）、取消退出不调、空白保存禁用）。
- 不依赖真实后端；验证管道正确（事件走对 store、气泡过滤对、笔记 CRUD 对），不验证视觉。
