# Zustand Stores（`stores/*.ts`）

> 每系统一个 store（CLAUDE.md）。共 8 个：`chatStore`（聊天）、`innerLifeStore`（内在状态快照）、`eventStore`（溯源时间线）、`desireStore` / `activityStore` / `memoryStore` / `evalStore`（四个快照 store）、`settingsStore`（背景外观，纯前端 UI 状态）。
> 范围：`stores/*.ts` 的 state 形状 + actions。
> 约定：**SSE 是主通道**（01-sse 分发表），store 的增量 action 由 SSE 驱动；REST 只喂初始快照。TS 类型字段名 = 后端 JSON 键（snake_case，零映射）。

## 0. 共享类型（`types/api.ts` 节选）

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
  kind: "message" | "speak" | "ask" | "think" | "mutter" | "initiate_chat";
  content: string;
  correlation_id: string;
  preloaded?: boolean;        // 历史回填消息：渲染时不逐字（loadHistory 写入）
};

type ChatState = {
  messages: ChatMessage[];
  isReplying: boolean;        // 发消息后等待回复中
  sendError: string | null;
  typedIds: Record<string, true>;  // 已逐字打完的 think id（speak/ask 等其同 correlation_id 的 think 打完才开打）
};
```

> `kind` 区分 Nyx 的 5 种产出（speak/ask/think/mutter/initiate_chat），渲染样式不同（03-chat-panel）；`role` 只决定左右气泡。

### actions

```typescript
addUserMessage(e: UserMessageEvent): void            // SSE user_message 回显（读 e.message）→ {role:"user", kind:"message"}
addSpeak(e: TextEvent<"speak">): void                // {role:"nyx", kind:"speak"}；correlation_id === pendingId 时才 clearTimeout(replyTimer) + isReplying=false + sendError=null
addAsk(e: TextEvent<"ask">): void                    // {role:"nyx", kind:"ask"}；correlation_id === pendingId 时才 clearTimeout(replyTimer) + isReplying=false + sendError=null
addThink(e: TextEvent<"think">): void                // {role:"nyx", kind:"think"}
addMutter(e: TextEvent<"mutter">): void              // {role:"nyx", kind:"mutter"}
addInitiateChat(e: TextEvent<"initiate_chat">): void // {role:"nyx", kind:"initiate_chat"}

sendMessage(text: string): Promise<void>  // 内部调 client.postChat(text)（client 契约见 05-client）
                                          // 成功：pendingId = 返回的 event_id + isReplying=true + sendError=null + 起 60s 超时 timer
                                          // postChat throw → catch → sendError = e.message（isReplying 未置，无需复位）
markTyped(id: string): void              // 把 think id 写入 typedIds（think 逐字 done 时调，解锁其后同 correlation_id 的 speak/ask）
loadHistory(): Promise<void>             // 并行 GET /api/events/log（六类文本事件）→ 合并按 timestamp 升序 → 按 id 去重 → preloaded:true 前置到 messages；历史 think 一并入 typedIds；失败 best-effort 不抛（不阻塞实时 SSE）
reset(): void                            // 新会话全清：clearTimeout(replyTimer) + messages/isReplying/sendError/typedIds 复位
```

### 关键决策

- **SSE 是聊天消息的唯一来源**：`sendMessage` 只 `POST`（拿 `{event_id}` 后 `isReplying=true`），**不本地 append**。用户消息靠 SSE `user_message` 回显上屏（localhost 往返 ~10ms，视觉无延迟）。好处：无「乐观消息 + SSE 回显」的**去重/替换**复杂度；`correlation_id` 沿事件一路一致，追溯无分歧（原则 5）。
- **isReplying 生命周期 + 60s 超时（必须取消）+ correlation 匹配**：`sendMessage` 成功时**存 `postChat` 返回的 `event_id` 到 module-level `pendingId`**（该 id = 后端 `user_message` 事件 id = 回复帧的 `correlation_id`），置 `isReplying=true` + `sendError=null` 并起 60s 超时 timer（`setTimeout`，回调置 `isReplying=false` + `sendError="回复超时"`、**不清 pendingId**——迟到回复仍需能匹配清 sendError）；`addSpeak`/`addAsk` 收到回复时**先判 `e.correlation_id === pendingId`**——匹配才 `clearTimeout` 取消 timer + 置 `isReplying=false` + `sendError=null`，非匹配（搭话/碎碎念等别的发言）只 append 不动生命周期。`think`/`mutter` 不结束回复（后必跟 `speak`）。`timer`/`pendingId` 都放 module-level（`let replyTimer`/`let pendingId`，**不进 store state**——store 状态须可序列化）。缺取消机制 = 真 bug：10s 收到回复，60s 时 timer 照样触发假「回复超时」。
- **消息顺序与时间戳**：SSE 顺序到达，直接 `push`，不排序——故 `ChatMessage` **不存 `timestamp`**（SSE `data` 无后端 `Event.timestamp`，见 01-sse §1；排序靠到达顺序，前端 `Date.now()` 只是近似，核心先行不需要）。

## 2. `innerLifeStore`

### state

```typescript
type InnerLifeState = {
  current: CurrentState | null;   // GET /api/state 快照；null = 尚未加载
  loading: boolean;
  error: string | null;
};
```

### actions

```typescript
refreshState(): Promise<void>   // 内部调 client.getState() → current；getState throw → catch → error（loading 复位）
updateEmotion(e: EmotionUpdateEvent): void  // SSE emotion_update → 覆盖 current 的 valence/arousal/emotion（emotion 走 isEmotionCategory 收窄）
```

### 关键决策

- **快照 + 增量**：进页面 `refreshState()` 拉全量 `CurrentState`；之后 `emotion_update` 事件只**局部覆盖** `valence`/`arousal`/`emotion` 三字段，不重新拉快照（省 REST + 反映实时变化）。
- **`emotion_update` 到达时 `current` 可能为 null**（快照未回）：不崩——`updateEmotion` 对 null 直接忽略（等 `refreshState` 回来覆盖）；App 层在 SSE `status === "open"` 时补一次 `refreshState`（01-sse §5）。
- `personality`/`values`/`energy*` 不变时不动，只在 `refreshState` 全量刷新（这些是慢变量，无对应高频事件）。

## 3. `eventStore`（骨架）

```typescript
type EventRecord = SseEvent & { received_at: number };

type EventState = {
  events: EventRecord[];   // 时间线，最新在前
  count: number;           // 收到的事件总数（含被 cap 截断的）
};

// actions
record(e: SseEvent): void   // unshift 头部（最新在前）+ count++；events 长度 > MAX_EVENTS(500) 时丢尾部最旧（pop）
loadHistory(events: BackendEvent[]): void  // 回填 GET /api/events/log：转 EventRecord 后与现有按 received_at 降序合并、按 event_id 去重；count 不计历史（只计 SSE 实时）
clear(): void               // 清空
```

### 关键决策

- **兜底不丢事件**：01-sse 分发表的 `default` 分支把未消费的 11 类事件都落这里，溯源面板后续直接读 `eventStore.events`，无需改 SSE 层。
- **内存上限**：`MAX_EVENTS = 500`，超出丢最旧但 `count` 累计，防长时间运行内存无限增长（溯源面板的「完整历史」走 `GET /api/events/log`，本 store 只存最近窗口）。

## 4. 快照 store（`desireStore` / `activityStore` / `memoryStore` / `evalStore`）

四个 store 对齐 `innerLifeStore` 的「REST 快照 + SSE 增量」模式：state = `{data|null, loading, error}` + `refresh()`。SSE 增量事件只带 id（不含完整对象），面板收到事件调 `refresh()` 重拉快照（01-sse §4 分发表）。

```typescript
// desireStore —— GET /api/desires
type DesireStoreState = { data: DesireState | null; loading: boolean; error: string | null };
refresh(): Promise<void>          // 内部调 client.getDesires() → data；throw → error + loading=false

// activityStore —— GET /api/activity
type ActivityStoreState = { data: ActivitySnapshot | null; loading: boolean; error: string | null };
refresh(): Promise<void>

// memoryStore —— GET /api/memories
type MemoryStoreState = { data: Memory[] | null; loading: boolean; error: string | null };
refresh(): Promise<void>

// evalStore —— GET /api/eval + GET /api/tokens（并行）
type EvalStoreState = { reports: EvalReport[] | null; tokens: TokenUsage[] | null; loading: boolean; error: string | null };
refresh(): Promise<void>          // Promise.all([getEval(), getTokens()]) → reports/tokens；任一 throw → error
```

### 关键决策

- **evalStore 是唯一「双字段」快照 store**：`reports` + `tokens` 两个端点并行拉（`Promise.all`），无对应 SSE 事件，仅挂载时拉取 + 面板内「刷新」按钮重拉（README §5）。
- **SSE 增量只触发 `refresh()`**：`desire_*` → `desireStore.refresh()`、`activity_*` → `activityStore.refresh()`、`memory_*` → `memoryStore.refresh()`。事件 content 只带 `{desire_id}`/`{activity_id}`/`{memory_id}`，不含完整对象，故重拉快照而非本地拼装。
- **`eventStore.count` 只计 SSE 实时**：`loadHistory` 回填的历史事件不进 `count`（`count` 语义 = 「收到的事件总数」含被 cap 截断的，历史回填不算收到）。

## 5. `settingsStore`（背景外观，纯前端）

```typescript
type SettingsState = {
  tint: string | null;   // 背景色调（十六进制色，null = 默认粉渐变）
  image: string | null;  // 背景图 data URL（null = 无图）
  setTint(tint: string | null): void;
  setImage(image: string | null): void;
  reset(): void;         // tint/image 均回 null
};
```

### 关键决策

- **无后端、无 SSE**：纯前端 UI 状态，读写只走内存，MVP 不持久化（重启回默认）。
- **tint 与 image 独立并存**：图铺底（`cover`）、色调无图时作纯色、图+色并存时叠一层半透明滤镜（`.app-bg-tint`），互不覆盖。

## 6. 测试（`tests/stores.test.ts`）

- **chatStore**：`addSpeak`/`addAsk`/`addThink`/`addMutter`/`addInitiateChat`/`addUserMessage` 各断言「正确转成 `ChatMessage`（role/kind/content/correlation_id）且 append」；`sendMessage` mock fetch 断言「请求 `/api/chat`、成功置 isReplying + 清 sendError、失败置 sendError」；`addSpeak` 断言 isReplying 复位 + clearTimeout 被调。**60s 超时**（Vitest fake timers）：`sendMessage` 成功后 `vi.advanceTimersByTime(60_000)` → `sendError="回复超时"` + `isReplying=false`；`sendMessage` 后立即 `addSpeak`（correlation 匹配）再 `advanceTimersByTime(60_000)` → **不**触发超时（timer 已取消）。**correlation 匹配**：非匹配 `correlation_id` 的 `addSpeak` 不清 timer（isReplying 保持 true、消息照常上屏）；迟到回复（超时后 correlation 仍匹配）清 sendError。
- **chatStore.loadHistory**：按 `timestamp` 升序前置 + `preloaded=true` + 历史 think 入 `typedIds`；已存在的 id 去重不重复前置；`getEventsLog` 失败 → best-effort 不抛、消息不变；`markTyped` 标记 + `reset` 清 `typedIds`。
- **innerLifeStore**：`refreshState` mock fetch 断言 current 被设置 + loading 状态机；`updateEmotion` 断言只覆盖三字段、`current=null` 时不崩。
- **eventStore**：`record` 断言 unshift（最新在前）+ count++；超 `MAX_EVENTS` 丢尾部最旧但 count 累计；`loadHistory` 回填历史 + 与现有按 `received_at` 降序去重。
- **四个快照 store**：`desireStore`/`activityStore`/`memoryStore` 各断言 `refresh()` 请求对端点 + `data` 落 store + `loading` 复位；`evalStore.refresh()` 并行 `getEval`+`getTokens`（fetch 恰 2 次）→ `reports`/`tokens` 落 store；`desireStore.refresh()` 失败 → `error` + `loading=false` + `data` 保持 null。
- **`isReady`（串行逐字纯函数）**：think 未打完 → speak 等（false）；think 打完 → speak 就绪（true）；无前置 think → 直接就绪；`preloaded` speak → 直接就绪；think 自身 → 恒就绪；不同 `correlation_id` 的 think 不阻塞。
- **`settingsStore`**：`setTint`/`setImage` 独立落 store 可并存；`reset()` 回 null。
- 全部 mock fetch/无真实后端；验证管道正确（事件走对 store、字段零映射），不验证视觉。
