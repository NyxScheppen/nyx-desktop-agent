# REST 客户端（`api/client.ts`）

> 薄 fetch 封装：端点函数 + 统一错误契约。被 `chatStore`、`innerLifeStore`、`usePresence`、快照 store、`readerStore` 和 `evalStore` 共享。
> 范围：`api/client.ts` 全部。`BASE_URL` 常量在此定义，SSE 与 REST 共用（01-sse §3）。
> 对齐后端：前端的基础设施独立成 spec，同底层模块总线系统和 `03-llm` 各自独立。

## 1. 函数签名 + 端点映射

下表 `/api/...` 业务路径是原有后缀。绑定角色后 `characterScope.scopedRequest` 将其改写为 `/api/characters/{id}/...`，写操作附 `X-Nyx-Selection-Revision`；GET 的 AbortController 在切换时取消。宿主 `/api/characters` 管理、`/api/observe` 和 `/api/ready` 不改写。无 scope 的旧辅助调用保留 Nyx 别名。

角色管理函数：`getCharacters`（GET 列表）、`importCharacter`（POST 卡包 FormData）、`selectCharacter`（PUT current）、`changeCharacter`（DELETE id 或 POST restore/retry-start）、`characterAssetUrl`（GET id/assets/key）。签名以 client.ts 为准。

```typescript
const BASE_URL = import.meta.env.DEV ? "" : "http://127.0.0.1:8000";

async function postChat(message: string, replyTo?: string, discussion?: boolean | null, annotation?: ReplyAnnotation): Promise<{ event_id: string; annotation_persistence?: "recorded" | "pending" }> // POST /api/chat
async function getPendingQuestions(): Promise<{ server_now: number; questions: PendingQuestion[] }> // GET /api/questions/pending
async function pauseQuestion(attemptId: string): Promise<PendingQuestion> // POST /api/questions/{id}/pause
async function resumeQuestion(attemptId: string): Promise<PendingQuestion> // POST /api/questions/{id}/resume
async function getState(): Promise<CurrentState>                                 // GET /api/state
async function postObserve(observation: PresenceObservation, signal?: AbortSignal): Promise<{ event_id: string }>  // POST /api/observe
async function getDesires(): Promise<DesireState>                                // GET /api/desires
async function getMemories(kind: MemoryKind | null = null): Promise<Memory[]>     // GET /api/memories?kind=
async function getMemorySearch(query: string, kind: MemoryKind | null = null): Promise<Memory[]> // GET /api/memories/search?q=&kind=
async function getRecentFacts(limit = 64, scope: FactScope = "profile"): Promise<MemoryFact[]> // GET /api/memories/facts?limit=&scope=
async function postKnowledgeSource(body: KnowledgeSourceRequest): Promise<KnowledgeSourceResult> // POST /api/knowledge/sources
async function getActivity(): Promise<ActivitySnapshot>                          // GET /api/activity
async function getActivityResults(params?): Promise<Activity[]>                  // GET /api/activity/results?limit=&offset=&activity_type=creation
async function getEventsLog(params?): Promise<BackendEvent[]>                    // GET /api/events/log?limit=&event_type=&correlation_id=
async function getEvalRecent(limit = 5, offset = 0): Promise<EvalRecord[]>      // GET /api/eval/recent?limit=&offset=
async function getEvalTotalTokens(): Promise<EvalStats>                          // GET /api/eval/total_tokens
async function getEvalPrompt(recordId: string): Promise<LlmPromptMessage[] | null> // GET /api/eval/{recordId}/prompt，cache:no-store

// ---- 阅读（06-reading-panel §6）----
async function getBooks(): Promise<BookListItem[]>                               // GET /api/books
async function getBookParagraphs(bookId: string, from: number, to: number): Promise<Paragraph[]>  // GET /api/books/{id}/paragraphs?from=&to=
async function getProgress(bookId: string): Promise<Progress>                    // GET /api/progress/{id}
async function putProgress(bookId: string, p: ProgressInput): Promise<void>      // PUT /api/progress/{id}（body 3 键，不含 read_count）
async function importBook(file: File): Promise<Book>                             // POST /api/books（multipart，FormData 不设 json 头）
async function evaluateImpulse(bookId: string, paragraphIndex: number, lastParagraphIndex: number): Promise<{ triggered: string[] }>  // POST /api/impulse/evaluate

// ---- 笔记（docs/frontend/07-reading-events.md §4）----
async function getNotes(bookId: string): Promise<UserNoteWithAnnotations[]>      // GET /api/notes/{bookId}
async function createUserNote(p: { book_id: string; paragraph_id?: string | null; content: string; selected_text?: string | null }): Promise<UserNote>  // POST /api/notes/user
async function updateUserNote(id: string, content: string): Promise<UserNote>    // PUT /api/notes/user/{id}
async function deleteUserNote(id: string): Promise<void>                         // DELETE /api/notes/user/{id}（204 无 body，直接 fetch + assertOk）
async function showNoteToNyx(noteId: string): Promise<Annotation | null>         // POST /api/notes/{noteId}/show-to-nyx（LLM 空/失败回 null）
async function checkChapterBoundary(bookId: string, nyxPosition: number): Promise<{ is_boundary: boolean; book_finished: boolean }>  // POST /api/notes/check-chapter-boundary

```

- 请求体键名 = 后端 JSON 键（snake_case 零映射）：`postChat` 发 `{message}`，有明确提问归属时附加 `reply_to`；`postObserve` 必须带稳定 `sample_id`，其余为 `{presence, window_title, idle_seconds, sampled_at}` 与可选 `character_id/selection_revision`。PresenceObservation 定义在 types/api.ts，signal 透传 fetch。重试保留相同 sample_id；409 表示过期/冲突采样，422 表示非法输入。
- **请求头**：`Content-Type: application/json`（FastAPI + Pydantic 体，错头会 422）。
- 返回值直接 JSON 反序列化后上抛给调用方，**不包裹** `{ok, data}`。

`postChat` 保留可选 discussion 的 null/true/false，末尾可选 annotation 为
`{reply_event_id, quote, comment}`。批注请求使用 `message=comment`，与 reply_to 互斥。
三个字段必须非空白严格字符串，ID 长1–128，quote/comment 各1–2000个 Unicode 字符，保留原样。
未知/跨角色锚点404，非 SPEAK/非连续原文/字段或互斥校验422，锚点查证或受理失败503。
带批注时可返回 annotation_persistence：仅查证反馈行存在为 recorded，否则 pending；反馈状态
查询失败也可成功受理并返回 pending。普通聊天无此可选字段。记录不等于事实更新完成。


### 知识摄入类型与结果（P2）

```typescript
type KnowledgeSourceRequest = { text?: string; url?: string };
type KnowledgeChunk = {
  chunk_id: string; source_topic: string; source_name: string;
  location: string | null; content: string;
};
type KnowledgeSourceResult = {
  created_memories: number; created_facts: number; chunks: KnowledgeChunk[]; errors?: string[];
};
```

`postKnowledgeSource` 按普通角色写请求附 selection revision。text/url 必须为字符串，长度分别
≤ 200000 / 2048，至少一项非空；支持两项并存。成功返回本次实际新增数与保存的原文块，重复
摄入可返回零新增。存在已保存块时，部分失败以 200 + `errors` 返回；没有保存块时，联网关闭
返回 409，其余抓取/摄入失败返回 400；请求校验失败返回 422。前端收到 200 后仍须检查 errors。
`MemoryFact.source_chunk_id` 是可空证据块 ID；旧事实保持 null，P2 不增加原文详情请求。

## 2. 错误契约（统一）

- **成功返回数据、失败 throw**。所有函数：fetch 网络错误（`TypeError`）与非 2xx 响应（读 body 错误信息后 `throw new Error(...)`）都上抛——**不返回 `{ok:false}`、不返回 null**。调用方自行 try/catch：
- 角色业务入口：未知 ID 404、选择版本过期 409、不可运行 503；不回退到当前角色或 Nyx。准入后的写请求即使切换，也继续交给原角色。
- **非 2xx 错误体形状**：后端是 FastAPI，错误体 = 默认 `{"detail": str}`（组合根未自定义错误响应）。client 读 `body.detail`，防御式兜底 `body.detail ?? body.error ?? JSON.stringify(body)`——任何形状都出非空 message，不 `undefined`。
  - `sendMessage`：catch → `sendError = e.message`（02-stores §1；失败时 `isReplying` 本就 false——成功才置 true，无需复位）。
  - `refreshState`：catch → `error = e.message`、`loading=false`（02-stores §2）。
  - `usePresence`：catch → `console.error` + 静默；原 FIFO 队首保留同一 sample_id，后续采样先重试队首，不被新状态覆盖（README §2）。

## 3. 边界

- client.ts 只做「序列化 + fetch + 反序列化 + 错误上抛」，**不含业务逻辑**（不置 isReplying、不写 store、不重试）——那归 store action / hook。
- **不运行时校验响应形状**：`res.json()` 返回后直接 cast（信任零映射，字段名 = 后端 JSON 键）。校验归 store/hook（如 01-sse §4.1 的 `typeof` 收窄）；client 加校验 = 边界越位。
- 不封装重试/超时（核心先行未请求）；`sendMessage` 的 60s 超时兜底在 store action 层做，不进 client。

## 4. 测试（`tests/api.test.ts`）

- 每个函数 mock `fetch`，断言请求 URL/method/请求体键 + 响应解析：
  - `postChat`：请求 `POST /api/chat`，保留 message/reply_to、discussion 三态和完整 annotation；
    解析 event_id 与可选 recorded/pending，普通请求不添加 annotation。
  - `getPendingQuestions` / `pauseQuestion` / `resumeQuestion`：请求提问快照和计时控制端点，解析 `server_now`、attempt 状态与暂停字段。
  - `getState`：`GET /api/state` → 返回 `CurrentState` 解析正确。
  - `getMemories` / `getMemorySearch`：分别请求 `/api/memories` 与 URL 编码后的
    `/api/memories/search?q=`；传入类别时附加 `kind`。
  - `getRecentFacts`：请求 `/api/memories/facts?limit=&scope=`，limit 在 client 侧限制到 `0..64`。
  - `postKnowledgeSource`：按角色 URL/selection revision 发送 text/url，原样返回计数、chunks 与部分成功 errors。
  - `postObserve`：`POST /api/observe`、body `{presence, window_title, idle_seconds, sampled_at}` + signal → 返回 `{event_id}` 解析正确。
  - `getDesires`：`GET /api/desires` → `DesireState` 解析正确。
  - `getActivity`：`GET /api/activity` → `ActivitySnapshot` 解析正确。
  - `getActivityResults`：`limit` / `offset` / `activity_type=creation` 拼入 `GET /api/activity/results`，`Activity[]` 解析正确。
  - `getEventsLog(params)`：`limit`/`event_type`/`correlation_id` 拼进 query。
  - eval：recent/total 路径正确；prompt 对 record id 做 URL 编码、禁用缓存并保留 `null`（旧记录无 prompt）。
- **错误契约**：非 2xx 响应（mock body `{"detail": "..."}`）→ throw（`Error`，message 含 `detail` 内容）；fetch 网络错误（reject `TypeError`）→ 上抛不吞；不返回 `{ok:false}`/null。
- 不依赖真实后端；验证管道正确（端点走对、键零映射、错误上抛），不验证视觉。
