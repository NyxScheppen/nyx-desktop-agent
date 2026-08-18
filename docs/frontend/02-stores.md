# Zustand Stores（`stores/*.ts`）

> 每系统一个 store（CLAUDE.md）。核心先行 3 个：`chatStore`（聊天）、`innerLifeStore`（内在状态快照）、`eventStore`（溯源时间线骨架）。
> 范围：`stores/chatStore.ts` / `stores/innerLifeStore.ts` / `stores/eventStore.ts` 的 state 形状 + actions。
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
};

type ChatState = {
  messages: ChatMessage[];
  isReplying: boolean;        // 发消息后等待回复中
  sendError: string | null;
};
```

> `kind` 区分 Nyx 的 5 种产出（speak/ask/think/mutter/initiate_chat），渲染样式不同（03-chat-panel）；`role` 只决定左右气泡。

### actions

```typescript
addUserMessage(e: UserMessageEvent): void          // SSE user_message 回显（读 e.message）→ {role:"user", kind:"message"}
addSpeak(e: TextEvent<TextEventType>): void        // {role:"nyx", kind:"speak"}；clearTimeout(replyTimer) + isReplying=false
addAsk(e: TextEvent<TextEventType>): void          // {role:"nyx", kind:"ask"}；clearTimeout(replyTimer) + isReplying=false
addThink(e: TextEvent<TextEventType>): void        // {role:"nyx", kind:"think"}
addMutter(e: TextEvent<TextEventType>): void       // {role:"nyx", kind:"mutter"}
addInitiateChat(e: TextEvent<TextEventType>): void // {role:"nyx", kind:"initiate_chat"}

sendMessage(text: string): Promise<void>  // 内部调 client.postChat(text)（client 契约见 05-client）
                                          // 成功：isReplying=true + sendError=null + 起 60s 超时 timer
                                          // postChat throw → catch → sendError = e.message（isReplying 未置，无需复位）
reset(): void                            // 清空 messages（新会话/测试用）
```

### 关键决策

- **SSE 是聊天消息的唯一来源**：`sendMessage` 只 `POST`（拿 `{event_id}` 后 `isReplying=true`），**不本地 append**。用户消息靠 SSE `user_message` 回显上屏（localhost 往返 ~10ms，视觉无延迟）。好处：无「乐观消息 + SSE 回显」的**去重/替换**复杂度；`correlation_id` 沿事件一路一致，追溯无分歧（原则 5）。
- **isReplying 生命周期 + 60s 超时（必须取消）**：`sendMessage` 成功置 true 并起 60s 超时 timer（`setTimeout`，回调置 `isReplying=false` + `sendError="回复超时"`）；`addSpeak`/`addAsk` 收到回复时**先 `clearTimeout` 取消 timer** 再置 false（`think`/`mutter` 不结束回复，因为后必跟 `speak`）。timer 引用放 module-level（`let replyTimer`，**不进 store state**——store 状态须可序列化）。缺取消机制 = 真 bug：10s 收到回复，60s 时 timer 照样触发假「回复超时」。
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
updateEmotion(e: EmotionUpdateEvent): void  // SSE emotion_update → 覆盖 current 的 valence/arousal/emotion
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
clear(): void               // 清空
```

### 关键决策

- **兜底不丢事件**：01-sse 分发表的 `default` 分支把未消费的 11 类事件都落这里，溯源面板后续直接读 `eventStore.events`，无需改 SSE 层。
- **内存上限**：`MAX_EVENTS = 500`，超出丢最旧但 `count` 累计，防长时间运行内存无限增长（溯源面板的「完整历史」走 `GET /api/events/log`，本 store 只存最近窗口）。

## 4. 测试（`tests/stores.test.ts`）

- **chatStore**：`addSpeak`/`addAsk`/`addThink`/`addMutter`/`addInitiateChat`/`addUserMessage` 各断言「正确转成 `ChatMessage`（role/kind/content/correlation_id）且 append」；`sendMessage` mock fetch 断言「请求 `/api/chat`、成功置 isReplying + 清 sendError、失败置 sendError」；`addSpeak` 断言 isReplying 复位 + clearTimeout 被调。**60s 超时**（Vitest fake timers）：`sendMessage` 成功后 `vi.advanceTimersByTime(60_000)` → `sendError="回复超时"` + `isReplying=false`；`sendMessage` 后立即 `addSpeak` 再 `advanceTimersByTime(60_000)` → **不**触发超时（timer 已取消）。
- **innerLifeStore**：`refreshState` mock fetch 断言 current 被设置 + loading 状态机；`updateEmotion` 断言只覆盖三字段、`current=null` 时不崩。
- **eventStore**：`record` 断言 unshift（最新在前）+ count++；超 `MAX_EVENTS` 丢尾部最旧但 count 累计。
- 全部 mock fetch/无真实后端；验证管道正确（事件走对 store、字段零映射），不验证视觉。
