# Zustand Stores（`stores/*.ts`）

## 对话续接状态（26）

`isSending` 只锁一个HTTP提交；`isReplying` 从逐ID `pendingReplies` 的 waiting 状态派生。每项保存本地单调时钟60秒期限、版本和 waiting/timed_out/dead_letter。
超时与失败通过 `replyNotice` 展示，不清除独立 `sendError`，不代表消息未受理。timed_out仍保留身份以接受迟到终局、避免自动绑定旧问题。
`loadHistory` 每次校正 GET chat/pending；快照整体校验、请求序号/逐项版本/角色scope/generation阻止迟到覆盖；truncated不按缺席删除。
同页重连沿用期限，reset取消全部timer并清证据/增代数，重载从快照重新给60秒；历史气泡本身不创建等待。
发送捕获模式、问题选择、批注；成功只清未被修改的快照。已有等待时不自动绑定另一个旧问题。网络丢回包显示提交结果未知，不自动重发。
完整状态、坏数据和乱序表见 [26 §7/§9](../specs/26-conversation-steering.md)。

> 每系统一个 store（CLAUDE.md）。现有聊天、内在状态、欲望、活动、记忆、设置、表达气泡、阅读与评估。
> 范围：`stores/*.ts` 的 state 形状 + actions。
> 约定：**SSE 是主通道**（01-sse 分发表），store 的增量 action 由 SSE 驱动；REST 只喂初始快照。TS 类型字段名 = 后端 JSON 键（snake_case，零映射）。

## 0. 共享类型（`types/api.ts` 节选）

### 角色归属与切换

现有领域 Store 只保存当前角色的展示快照。`characterStore` 保存注册列表、当前 ID、服务端 selection revision、切换状态/错误和本地 generation；`characterScope` 在 action 入口捕获不可变 scope，所有 success/error/finally/timer/retry 核对同一 scope。A→B→A 的旧 A 回调也失效。

切换串行执行：失效旧 scope/取消读请求，关闭旧 SSE，重置领域展示、回复等待、通知和阅读会话；PUT 成功后绑定新 scope、加载历史/快照、恢复角色草稿与最近书籍/阅读位置。失败重新查询实际选择并恢复。后台轮询在切换中不采用旧选择。无聊天或任务 POST 自动网络重试。

阅读冲突重试固定原角色、书籍、session 与 payload；切换后不能从新 Store 拼装旧请求。全局 settings 保留，草稿和最近书籍按角色保存。完整生命周期契约见 [14-character-cards](../specs/14-character-cards.md)。

```typescript
// 情绪/精力/活动枚举值：与后端 StrEnum 值一致（snake_case）
type EmotionCategory = "neutral" | "happy" | "sad" | "angry" | "worried" | "shy" | "sleepy" | "thinking";
type EnergyState = "energetic" | "okay" | "tired" | "exhausted" | "drained";

type Personality = { openness: number; conscientiousness: number; extraversion: number; agreeableness: number; neuroticism: number };
type Values = { attitude_to_human: number; ai_identity_acceptance: number; altruism: number; optimism: number };
// ShortTermDesire / ActivityType 等镜像字段见 01-types；核心先行仅 innerLife 面板用到 Personality/Values + energy 相关。

type CurrentState = {
  valence: number;                // [-1, 1]
  arousal: number;                // [0, 1]
  emotion: EmotionCategory;
  personality: Personality;
  values: Values;
  energy: number;                 // [0, 100]
  energy_state: EnergyState;
  current_activity: string | null;   // ActivityType 值；核心先行仅展示，用 string 放宽
  active_desires: unknown[];      // 核心先行不消费，占位
};

// SSE 帧是判别联合（完整契约见 01-sse §2），各 action 的精确入参见下面 §1/§2
type Presence = "online" | "away" | "busy";
```

## 1. `chatStore`

### state

```typescript
type ChatMessage = {
  id: string;                 // event_id
  role: "user" | "nyx";
  kind: "message" | "speak" | "ask" | "think" | "initiate_chat" | "reading_question";
  content: string;
  correlation_id: string;
  timestamp: number;           // 后端 Event.timestamp，epoch 秒
  preloaded?: boolean;        // 历史回填消息：渲染时不逐字（loadHistory 写入）
  subtype?: QuestionSubtype;         // kind==="reading_question" 才有（提问四子型）
  selectedText?: string | null;      // reading_question 引文
  attempt_id?: string;               // ASK/READING_QUESTION/INITIATE_CHAT 的等待 attempt
  expires_at?: number;               // 等待回应计时终点（epoch 秒）
  paused_at?: number | null;          // 非空：计时暂停
  question_status?: QuestionStatus;
  annotation?: ReplyAnnotation;      // 用户批注消息保留原始锚点、引用与评论
};

type ChatState = {
  messages: ChatMessage[];
  isReplying: boolean;        // 至少一项仍在 waiting
  isSending: boolean;         // 单个 HTTP 提交在途
  generation: number;         // reset 使旧回调失效
  pendingReplies: Record<string, { status: "waiting" | "timed_out" | "dead_letter"; deadline: number; version: number }>;
  pendingTruncated: boolean;
  replyNotice: string | null;
  sendError: string | null;
  discussionMode: boolean | null; // null 自动（默认），true 讨论，false 陪伴；reset 恢复 null
  annotationDraft: ReplyAnnotation | null;
  annotationPersistence: Record<string, "pending" | "recorded">; // 原始 [reply_event_id, comment] JSON 键
  typedIds: Record<string, true>;  // 已逐字打完的 nyx 文本 id（后一条同 correlation_id 的 nyx 文本等其打完才开打）
  seenIds: Record<string, true>;   // 本角色本次前端会话中已开始显示的 nyx 文本 id；重新挂载时不再逐字
  replyTargetId: string | null;    // 当前选中的等待提问；reset 同时清空
  serverTimeOffset: number;        // 服务端时间 - 浏览器时间（计时显示校正）
};
```

> `kind` 区分普通文本、读书提问和三类浏览 turn（03-chat-panel）；`role` 只决定左右气泡。

### actions

```typescript
addUserMessage(e: UserMessageEvent): void            // SSE user_message 回显（读 e.message）→ {role:"user", kind:"message"}
addAnnotationFeedback(e: AnnotationFeedbackEvent): void // 合并已记录回执，不 append 聊天消息
addSpeak(e: TextEvent<"speak">): void                // {role:"nyx", kind:"speak"}；合法覆盖列表逐项完成等待；旧协议仅按 correlation_id 完成一项
addAsk(e: TextEvent<"ask">): void                    // {role:"nyx", kind:"ask"}；合法覆盖列表逐项完成等待；旧协议仅按 correlation_id 完成一项
addThink(e: TextEvent<"think">): void                // {role:"nyx", kind:"think"}
addInitiateChat(e: TextEvent<"initiate_chat">): void // {role:"nyx", kind:"initiate_chat"}；保留 attempt_id/expires_at
addReadingTurn(e: ReadingQuestionEvent): void  // {role:"nyx",kind:"reading_question"}; subtype/selectedText 落字段
pauseQuestion(attemptId: string): Promise<boolean>  // POST /api/questions/{id}/pause
resumeQuestion(attemptId: string): Promise<boolean> // POST /api/questions/{id}/resume
selectQuestion(attemptId: string | null): void // 选择或取消提问回复
setDiscussionMode(mode: boolean | null): void // 选择本角色当前会话的讨论模式
beginAnnotation(eventId: string, quote: string): void // 仅已有 SPEAK 的连续原文，保留原样
updateComment(text: string): void
cancelAnnotation(): void
sendAnnotation(): Promise<boolean> // message=comment，附完整 annotation/discussion，跳过 reply_to
sendMessage(text: string): Promise<boolean>  // 内部调 client.postChat，带当前 discussionMode；成功 true，失败/忙 false
                                          // 成功：按 event_id 建立逐项等待；已有完成证据时不复活 timer
                                          // 无回复等待时才自动绑定唯一提问；显式选择仍优先，按发送快照提交
                                          // postChat throw → sendError；finally 只释放 HTTP 锁，保留其他等待
markTyped(id: string): void              // 把 nyx 文本 id 写入 typedIds（逐字 done 时调，解锁其后同 correlation_id 的下一条 nyx 文本）
markSeen(id: string): void               // 正文首次 ready、实际开始显示时按 event_id 记入 seenIds；不提前解锁后续消息
loadHistory(): Promise<void>             // 并行查询六类文本及 annotation_feedback，合并消息/持久回执，再取 pending 快照；失败 best-effort
reset(): void                            // 递增本地 generation，取消 timer，清空消息/回复/打字机/批注草稿及回执状态
```

### 关键决策

- **讨论三态**：`discussionMode` 默认 null；发送时作为 `ChatRequest.discussion` 传给后端，
  不以真假值合并 null/false。`reset()` 同时恢复自动模式；切换模式只影响后续发送。

- **批注发送与回执**：`sendMessage`/`sendAnnotation` 共用发送锁、pending tracker、提前到达回复集合
  和 60s timer。批注保留普通输入草稿与已选提问，只跳过本次 waiting 绑定；失败保留批注，成功仅
  清掉捕获时同一草稿对象，发送中编辑或新选引用不会被清掉。键为原始
  `JSON.stringify([reply_event_id, comment])`，不 trim。发送前建立 pending，SSE 与历史回执只提升
  recorded；POST 的 pending 和晚到历史不得降级。canonical 回执确认所有同键用户消息，表示
  “反馈已记录”，不表示事实更新或反思完成。无回执保守显示“反馈持久化待确认”。
- **本地 reset 与异步历史**：发送、timer 和 `loadHistory` 同时捕获角色 scope 与本地 generation。
  每次 await 后，包括事件查询、pending 快照的成功与 catch 分支，均核对两者；独立 reset 即使
  未切角色也使旧历史、回执与 serverTimeOffset 回包失效。角色切换/reset 清空批注和本地回执。

- **SSE 是聊天消息的唯一来源**：`sendMessage` 只 `POST`（拿 `{event_id}` 后登记对应等待），**不本地 append**。用户消息靠 SSE `user_message` 回显上屏（localhost 往返 ~10ms，视觉无延迟）。好处：无「乐观消息 + SSE 回显」的**去重/替换**复杂度；`correlation_id` 沿事件一路一致，追溯无分歧（原则 5）。
- **逐项等待与完成**：期限、快照和乱序规则见本文「对话续接状态」。timer 句柄保存在模块内，store 只保存可序列化状态。THINK 不结束等待；SPEAK/ASK 仅完成实际覆盖项，不清除独立提交错误。
- **消息顺序与时间戳**：实时与历史都按 id 去重，合并后全局按 timestamp 稳定升序排列，
  相同时按用户消息、think、对外文本排列，同等级按 event id 排列，避免回填令回复先于用户。
  缺失/非有限或 Date 不可表示的时间戳丢弃。
  非法帧在任何等待/未读副作用前丢弃；重复主动消息也不能重新点亮已读标记。
  每条 `ChatMessage.timestamp` 必须复制 SSE 的后端 `Event.timestamp`。历史回填复制
  `BackendEvent.timestamp`，因此重连/重启前后使用同一来源；禁止用 `Date.now()` 替代事件时间。
- **提问归并与回复归属**：同一 `attempt_id` 的 `ASK` 与 `READING_QUESTION` 只显示一条，优先
  保留读书事件的展示字段。历史回填后调用 pending 快照恢复计时状态；只有一个等待交互时发送
  自动带 `reply_to`，多个等待交互必须由用户选择，否则不发送并显示错误。暂停状态显示剩余时长，
  恢复后继续倒计时。所有不在 pending 快照里的 attempt 关闭本地 waiting 状态；只有
  `question_status === "waiting"` 的提问气泡显示计时和暂停/继续操作，用户发送成功后目标先标记
  `claimed`，避免下一条消息继续携带旧 `reply_to`。
- **主动搭话计时**：实时 `INITIATE_CHAT` 的 `attempt_id`/`expires_at` 原样进入消息；等待状态的
  气泡复用提问的服务端时间校正与“剩余 N 秒/等待超时确认”显示，但不增加暂停/继续按钮。
  重连和角色恢复时仍由历史事件与 pending 快照补齐状态；旧事件缺字段时不显示虚假倒计时。

## 2. `innerLifeStore`

### state

```typescript
type InnerLifeState = {
  current: CurrentState | null;   // GET /api/state 快照；null = 尚未加载
  error: string | null;
};
```

### actions

```typescript
refreshState(): Promise<void>   // 内部调 client.getState() → current；getState throw → catch → error
updateEmotion(e: EmotionUpdateEvent): void  // SSE emotion_update → 覆盖 current 的 valence/arousal/emotion（emotion 走 isEmotionCategory 收窄）
```

### 关键决策

- **快照 + 增量**：进页面 `refreshState()` 拉全量 `CurrentState`；之后 `emotion_update` 事件只**局部覆盖** `valence`/`arousal`/`emotion` 三字段，不重新拉快照（省 REST + 反映实时变化）。
- **`emotion_update` 到达时 `current` 可能为 null**（快照未回）：不崩——`updateEmotion` 对 null 直接忽略（等 `refreshState` 回来覆盖）；App 层在 SSE `status === "open"` 时补一次 `refreshState`（01-sse §5）。
- `personality`/`values`/`energy*` 不变时不动，只在 `refreshState` 全量刷新（这些是慢变量，无对应高频事件）。

## 3. 快照 store（`desireStore` / `activityStore`）

两个 store 对齐 `innerLifeStore` 的「REST 快照 + SSE 增量」模式：state = `{data|null, error}` + `refresh()`。SSE 增量事件只带 id（不含完整对象），面板收到事件调 `refresh()` 重拉快照（01-sse §4 分发表）。

### `memoryStore`

```typescript
type MemoryStoreState = {
  data: Memory[] | null;
  facts: MemoryFact[] | null;
  query: string;
  kind: MemoryKind | null;
  factScope: FactScope;
  error: string | null;
  factsError: string | null;
  refresh(): Promise<void>;
  search(query: string, kind?: MemoryKind | null): Promise<void>;
  selectFactScope(scope: FactScope): Promise<void>;
};
```

`refresh()` 并行加载当前类别与查询对应的记忆列表，以及当前事实 scope 中最多 64 条有效事实；事实请求失败只设置
`factsError`，不清空或阻塞 `data`。`search()` 只更新记忆列表，事实图保持隔离；空查询恢复当前类别列表。`selectFactScope()` 清空旧图并请求所选作用域。两类请求各自用代次保护晚到回包。`memory_created` / `memory_promoted` 的 SSE 分发仍只调用一次
`refresh()`，从而同时刷新记忆和事实图。

```typescript
// desireStore —— GET /api/desires
type DesireStoreState = { data: DesireState | null; error: string | null };
refresh(): Promise<void>          // 内部调 client.getDesires() → data；throw → error

// activityStore —— GET /api/activity + 创作 results + GET /api/tasks（并行）
type ActivityStoreState = {
  data: ActivitySnapshot | null;
  results: Activity[] | null;
  tasks: AssignedTask[] | null;
  error: string | null;
  taskError: string | null;
  creatingTask: boolean;
  resultsLoading: boolean;
  hasMoreResults: boolean;
};
refresh(): Promise<void>          // 时间线 + 最近 12 条创作 + 任务快照
loadMoreResults(): Promise<void>  // offset=results.length，追加下一批 12 条
createWebTask(url): Promise<boolean>
createBrowserSurveyTask(url): Promise<boolean>
createBookTask(bookId, targetParagraph): Promise<boolean>
```

### 关键决策

- **三字段快照 store**：`activityStore`（`data`+`results`+`tasks`）并行拉时间线、创作首页和委派任务；创作查询固定 `activity_type=creation`，每次请求 13 条、展示 12 条，用多出的一条判断是否显示“加载更多”。
- **分页响应受刷新世代保护**：`refresh()` 开始即推进内部世代；`loadMoreResults()` 只在世代和发起时的 `results` 引用都未变化时追加，旧分页的成功/失败/收尾不会覆盖新首页状态。
- **SSE 增量只触发 `refresh()`**：`desire_*` → `desireStore.refresh()`、`activity_*` / `task_updated` → `activityStore.refresh()`。事件 content 只带 id/status 等最小事实，不含完整对象，故重拉快照而非本地拼装。

## 4. `settingsStore`（背景外观 + 字体大小，纯前端）

```typescript
type FontScale = "small" | "medium" | "large";

type SettingsState = {
  tint: string | null;      // 背景色调（十六进制色，null = 默认粉渐变）
  image: string | null;     // 背景图 data URL（null = 无图）
  fontScale: FontScale;     // 字体大小档位（"small" | "medium" | "large"，默认 "medium"）
  setTint(tint: string | null): void;
  setImage(image: string | null): void;
  setFontScale(fontScale: FontScale): void;
  reset(): void;            // tint/image 回 null、fontScale 回 "medium"
};
```

### 关键决策

- **无后端、无 SSE**：纯前端 UI 状态，读写只走内存，MVP 不持久化（重启回默认）。
- **tint 与 image 独立并存**：图铺底（`cover`）、色调无图时作纯色、图+色并存时叠一层半透明滤镜（`.app-bg-tint`），互不覆盖。
- **fontScale 驱动 `--text-scale`**：App 在 `.game-shell` 上注入 `--text-scale`（0.9/1.0/1.12），`body`/`.game-shell` 用 `font-size: calc(1rem * var(--text-scale))` 统一缩放。

## 5. `announceStore`（头像旁临时气泡，纯前端呈现）

```typescript
type AnnounceKind = "mutter" | "activity";
type Announcement = { id: string; kind: AnnounceKind; text: string };

type AnnounceState = {
  items: Announcement[];
  announce(kind: AnnounceKind, text: string): void; // 追加 + 按 kind 时长到时 dismiss
  dismiss(id: string): void;                         // 摘除指定气泡
};
```

### 关键决策

- **纯呈现层、无后端/无 SSE 增量语义**：`announce` 由 SSE 分发表驱动（`mutter` → `announce("mutter", …)`、`activity_end` → `announce("activity", …)`），但 store 本身只管「临时气泡队列」：追加 → `setTimeout` 到时 `dismiss`。淡出视觉由 CSS `announce-pop` 动画承担（`AnnounceLayer` 读 `items` 渲染 + 按 `ANNOUNCE_DURATION[kind]` 设动画时长），store 到时摘除 DOM 节点。
- **不落聊天历史**：碎碎念只进 `announceStore`（瞬时气泡），不进 `chatStore` 历史——`dispatch` 把 `mutter`/`reading_mutter` 统一路由到 `announce("mutter", …)`（08 §3 迁移后原 `mutterStore` 已删），`HISTORY_TYPES` 不含 `mutter`。
- **时长按 kind**：`mutter` 4s、`activity` 7s（产出句子更长）。id 用模块级自增（`announce-N`），不依赖 `Date.now`（测试可预测）。

## 6. `readerStore`（阅读：书架/进度/追赶 + 笔记）

> 阅读系统唯一 store（06-reading-panel §3 + 07-reading-events §3 笔记）。追赶 timer 放 module-level（不进 store state，同 chatStore 的逐项 timer 约定）。

### state

```typescript
type NyxStatus = "idle" | "reading" | "waiting";   // 派生态，不落 store（nyxStatusOf 纯函数）

type ReaderState = {
  books: BookListItem[];            // 书架快照
  booksError: string | null;
  bookId: string | null;            // 当前打开的书（null = 未开书）
  totalParagraphs: number;          // 当前书总段数（openBook 从 books 取）
  paragraphs: Paragraph[];          // 当前窗口段落
  windowFrom: number;               // 窗口起始 index（1-based）
  userPosition: number;             // 用户读到第几段（1-based）
  nyxPosition: number;              // Nyx 读到第几段（1-based）
  readingSpeed: number;             // 字符/秒（10–200）
  readCount: number;                // 读完几遍（>=1 可重读）
  progressRevision: number;         // 后端 CAS 版本；只接受严格更新的服务端快照
  notes: UserNoteWithAnnotations[]; // 当前书用户笔记（含批注）
  notesError: string | null;
};
```

### actions

```typescript
// —— 06：书架/进度/追赶循环 ——
loadBooks(): Promise<void>                 // GET /api/books → books
openBook(bookId: string): Promise<void>    // getProgress → 会话态 + totalParagraphs（从 books 取）+ 拉首窗；nyx<user 时 startCatchup
closeBook(): void                          // stopCatchup + 全量复位（含 notes）
syncPosition(next: number): Promise<void>  // 位置同步（当前读到段同步）：clamp [1, total] → putProgress + 前翻逐段补发 evaluateImpulse + 越窗口边界重拉 + startCatchup
setReadingSpeed(speed: number): Promise<void>
startCatchup(): void                       // setTimeout 秒级推进；段落未加载/过短兜底 1s
stopCatchup(): void
advanceNyx(): void                         // nyxPosition += 1（不超 userPosition）；收尾时落库 nyx_position
applyProgressEvent(e: ReadingProgressEvent): void // SSE 快照：当前书 + revision/位置单调才应用
reread(): Promise<void>                    // 复位 userPosition/nyxPosition=1（read_count 不碰）

// —— 07：笔记 ——
loadNotes(): Promise<void>                 // GET /api/notes/{bookId} → notes
addNote(p: { book_id; paragraph_id?; content; selected_text? }): Promise<void>   // POST → 归一 {…note, annotations: []} unshift
updateNote(id: string, content: string): Promise<void>  // PUT → 覆盖 7 键保留 annotations
deleteNote(id: string): Promise<void>      // DELETE → 本地移除该条（连同其批注）
showToNyx(noteId: string): Promise<void>   // POST show-to-nyx → 返回 Annotation | null → append 到 annotations（不整表重拉）
```

### 关键决策

- **派生态不落 store**：`nyxStatusOf(bookId, nyxPosition, userPosition)` 纯函数派生 `idle/reading/waiting`，ReaderView 用它展示 Nyx 正在追赶或等待；`catchupDurationMs` / `computeWindow` / `paginate`（真分页，08 §5.1）同为可测纯函数（06）。
- **追赶循环秒级**：`startCatchup` 用 module-level `catchupTimer`，`catchupDurationMs = clamp(字数/速度, 1, 30) 秒`；`advanceNyx` 不超 `userPosition`，追上后落库 `nyx_position`（否则重载读到陈旧落后值会重追、重放 BOOK_FINISHED → read_count 重复 ++）。
- **进度 SSE 增量**：`reading_progress` 携带完整进度快照。`applyProgressEvent` 只接受当前
  `bookId`、严格大于 `progressRevision` 且不让 `nyxPosition` 回退的事件；异书、重复/旧
  revision 和回退快照直接丢弃。应用后根据 Nyx 是否落后启动或停止追赶，保留本地尚未提交的
  `userPosition`，避免后台/其它窗口的进度覆盖用户正在拖动的状态。
- **读书 turn 迁出气泡流**：`reading_question`/`reading_association` 并进 `chatStore`（`addReadingTurn`），不再进 `readerStore`；`reading_mutter` 走 `announceStore`。readerStore 只留书架/进度/追赶/笔记。
- **笔记「给尼克斯看」本地 append**：`showToNyx` 成功把完整 `Annotation` append 到该 note（不整表重拉避免抖动）；LLM 空/失败回 `null` 不 append；失败静默记 `notesError`。用户笔记与 Nyx 章末整合记忆严格分离（后者落 memory 不上屏）。
- **书签请求按书隔离**：`loadBookmarks` 捕获发起时的 `bookId`，成功或失败回包仅在该书
  仍为当前书时更新 `bookmarks/bookmarksError`，切书或关书后的旧回包直接丢弃。

## 7. `evalStore`

- `refresh()` 按 20 条一页递增 offset 读取全部历史评估记录，再读取累计 token；每次刷新清空 prompt 详情缓存。请求世代保护会丢弃旧刷新响应。
- `loadPrompt(recordId)` 只在用户展开记录时请求；成功结果（含 `null` 和空数组）按 record id 缓存，重复展开不重取。
- loading/error 均按 record id 隔离；失败不写成功缓存，下一次展开或“重试”可重新请求。请求返回时若该记录已不在当前 recent 列表，丢弃晚到结果。
- `EvalPanel` 用原生 `<details>/<summary>` 展开，按 role 显示转义后的 `<pre>` 文本；长 prompt 在固定最大高度内滚动，不截断内容。

## 8. 测试（`tests/stores.test.ts`）

- **chatStore**：覆盖事件到消息的字段映射、逐 ID 等待与独立发送锁、60 秒单调期限及迟到终局、合法覆盖与旧协议、非法快照/截断、SSE 先于 POST、快照请求乱序、reset/角色代数隔离，以及发送期间编辑草稿和等待提问绑定。fake timers 验证同页重连不延期、整页重载重新计时、完成后旧 timer 不报超时。
- **chatStore.loadHistory**：新旧消息统一按 `timestamp` 稳定升序 + `preloaded=true` + 历史 think 入 `typedIds`；与实时消息按 id 去重；非法时间戳丢弃；`getEventsLog` 失败 → best-effort 不抛、消息不变；`markTyped`/`markSeen` 标记 + `reset` 清两种播放状态。
- **innerLifeStore**：`refreshState` mock fetch 断言 current 被设置；`updateEmotion` 断言只覆盖三字段、`current=null` 时不崩。
- **两个快照 store**：`desireStore` 断言 `refresh()` 请求对端点 + `data` 落 store；`activityStore.refresh()` 并行 `getActivity`+`getActivityResults`+`getTasks`（fetch 恰 3 次）并验证任务创建回写；`desireStore.refresh()` 失败 → `error` + `data` 保持 null。
- **`isReady`（串行逐字纯函数）**：每条 nyx 文本消息等「同 `correlation_id` 且在其之前」的 nyx 文本消息都打完（入 `typedIds`）才就绪；无前置 nyx 文本 → 直接就绪；`preloaded` nyx 文本与 user 消息 → 恒就绪；不同 `correlation_id` 的 nyx 文本不阻塞。
- **`settingsStore`**：`setTint`/`setImage` 独立落 store 可并存；`reset()` 回 null。
- **`announceStore`**：`announce` 追加临时气泡（kind/text 落 store、id 唯一）；`dismiss` 摘除指定 id 其余保留；`advanceTimersByTime(ANNOUNCE_DURATION[kind])` 到时自动 dismiss。
- **`readerStore`（06 + 07）**：`loadBooks` 落 books；`openBook` mock getProgress+getBookParagraphs → 会话态 + totalParagraphs、nyx<user 时 startCatchup；`syncPosition` 前翻 putProgress+evaluateImpulse、回翻不评估，冲突重试保留服务端更靠前的 Nyx 位置，显式重读允许回退；`applyProgressEvent` 覆盖完整服务端快照但过滤异书、旧/重复 revision 和 Nyx 回退；`paginate` 真分页纯函数（贪心封页/空/溢出/GAP_PX）；追赶循环 fake timers 推进/收尾/clearTimeout 不叠加；`loadNotes`/`addNote`（unshift 归一）/`updateNote`（保留 annotations）/`deleteNote`/`showToNyx`（append 不重拉、null 不 append）。
- **`evalStore`**：refresh 分页读取全部记录并清详情；展开后逐 id 加载、成功缓存、失败可重试、旧记录 `null` 与真实空数组区分；面板安全渲染 Unicode/换行/HTML 字面文本。
- 全部 mock fetch/无真实后端；验证管道正确（事件走对 store、字段零映射），不验证视觉。
