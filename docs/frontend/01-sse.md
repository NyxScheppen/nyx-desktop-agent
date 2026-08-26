# SSE 数据流（`hooks/useSSE.ts`）

> 前端实时数据的**主通道**。后端广播全部事件（design §3.2），本 spec 定义 `useSSE` hook 契约、SSE 帧格式、事件→store 分发表、重连容错。
> 范围：`hooks/useSSE.ts` + `api/client.ts` 里的 SSE 相关部分。

## 1. 端点与帧格式

- 端点：`GET /api/events`（SSE，tech-ref §4）。
- 每个事件一条；`event:` 行 = `EventType` 成员值（小写 snake_case，如 `speak` / `emotion_update` / `activity_end`）。
- `data:` 行 = **`event.content` 展开 + `event_id` + `correlation_id`**：

```
event: speak
data: {"event_id":"…","correlation_id":"…","content":"…"}
```

- `data` 的键 = `{event_id, correlation_id}` + `event.content` 的键。`content` 的键从不与 `event_id`/`correlation_id` 冲突（生产方不产这两个键，展开安全——tech-ref §4）。
- 各事件 `content` 形状由生产方 spec 定义，核心先行只依赖四个：
  - `speak` / `ask` / `mutter` / `initiate_chat` → `{content: string}`
  - `think` → `{content: string}`（内心话，仅日志/聊天区灰色展示）
  - `emotion_update` → `{valence, arousal, emotion}`（12-inner-life 定义）
  - `user_message` → `{message: string}`（main.py 裸载荷，非 internal_text_event，键名与文本事件不同）

## 2. TS 类型

```typescript
// types/api.ts（片段）

/** SSE 帧：按 event 值判别联合——键名错位在编译期即拦。 */
type SseBase = {
  event_id: string;        // 事件唯一 id
  correlation_id: string;  // 溯源：上游 correlation_id（根事件 = 自身 id）
};

type TextEventType = "speak" | "ask" | "think" | "mutter" | "initiate_chat";
type TextEvent<T extends TextEventType> = SseBase & { event: T; content: string };

type UserMessageEvent = SseBase & { event: "user_message"; message: string };

type EmotionUpdateEvent = SseBase & {
  event: "emotion_update";
  valence: number;
  arousal: number;
  emotion: EmotionCategory;
};

type ReflectionDoneEvent = SseBase & {
  event: "reflection_done";
  story: string;         // 本轮反思产出的新故事片段
  story_is_new: boolean; // true = 故事真新增（去重通过）；false = 与已有片段重复、未追加
};

/** 探索地图节点：search = 搜索动作（url 空），web = 访问的网页。 */
type ExplorationNode = { name: string; url: string; kind: "search" | "web" };

/** 探索实时进度帧（exploration_step）：探索链每访问一个节点推一次。 */
type ExplorationStepEvent = SseBase & {
  event: "exploration_step";
  activity_id: string;
  node: ExplorationNode;
};

// 未消费的 12 类：无消费者，payload 保持宽松。
type OpaqueEvent = SseBase & {
  event: "clock_tick" | "observation_state" | "reflection"
    | "memory_created" | "memory_promoted"
    | "desire_generated" | "desire_satisfied" | "desire_expired"
    | "activity_start" | "activity_end" | "activity_interrupted";
} & Record<string, unknown>;

// 遭遇（19-encounter）：start 文本 + 选项、choice 选择、end 结局（choice 无消费者）。
type EncounterKind = "desire_chat" | "random_event" | "growth_moment";
type EncounterOption = { index: number; text: string };
type EncounterStartEvent = SseBase & {
  event: "encounter_start";
  encounter_id: string;
  kind: EncounterKind;
  text: string;
  options: EncounterOption[];
};
type EncounterChoiceEvent = SseBase & {
  event: "encounter_choice";
  encounter_id: string;
  option_index: number;
  option_text: string;
};
type EncounterEndEvent = SseBase & {
  event: "encounter_end";
  encounter_id: string;
  kind: EncounterKind;
  option_index: number;
  option_text: string;
  ending: string;
  consequences: Record<string, unknown>; // 前端不读后果细节，只触发快照 refresh
};

type SseEvent =
  | TextEvent<"speak"> | TextEvent<"ask"> | TextEvent<"think">
  | TextEvent<"mutter"> | TextEvent<"initiate_chat">
  | UserMessageEvent | EmotionUpdateEvent | ReflectionDoneEvent
  | ExplorationStepEvent
  | EncounterStartEvent | EncounterChoiceEvent | EncounterEndEvent
  | OpaqueEvent;

type ConnectionState = "connecting" | "open" | "closed";
```

> 字段名 = 后端 JSON 键（snake_case 原样，零映射），见 README §4 命名约定。

## 3. `useSSE` hook 契约

```typescript
// hooks/useSSE.ts
function useSSE(dispatch: (e: SseEvent) => void): ConnectionState;
```

- **入参**：`dispatch` —— 每个解析成功的帧调用一次，由调用方（App 层）决定路由到哪个 store（分发表见 §4）。
- **返回**：`ConnectionState`，供 App 显示连接状态（右上角「已连接/重连中」）。
- **行为**：
  1. `useEffect` 里 `new EventSource(BASE_URL + "/api/events")`，`BASE_URL` 来自统一常量（空 = 相对路径，走 Vite proxy 同源转发到后端 8000）。
  2. 对 24 个 `EVENT_TYPES` 逐个 `addEventListener(type, …)`（后端每条带 `event:` 行，命名事件只能按类型监听，`onmessage` 收不到）→ `JSON.parse(e.data)` → 校验 `event_id`/`correlation_id` → 拼 `SseEvent` → `dispatch`。
  3. `onopen` / `onerror`：更新 `ConnectionState`。`EventSource` 浏览器原生自动重连（`onerror` 时置 `connecting`），后端重启后自动恢复，无需手写重连循环。
  4. cleanup：`source.close()`（防重复挂载泄漏）。
- **解析失败**：`JSON.parse` 抛错 → `console.error` + 跳过该帧（不崩整个流）；`data` 缺 `event_id`/`correlation_id` 时同样跳过（防御，正常不触发）。
- **`ConnectionState` 三态触发点**：
  - 初始态 = `connecting`（挂载即 `new EventSource`）。
  - `onopen` → `open`；`onerror` → `connecting`（EventSource 原生自动重连中）。
  - cleanup（组件卸载）→ `closed`（`source.close()` 后置，生命周期结束不再重连）。
  - 无「后端主动关闭 → closed」场景：后端关流触发 `onerror` 走自动重连，不进 `closed`。

> 命名 `useSSE` 为 CLAUDE.md 前端规范点名；`BASE_URL` 抽到 `api/client.ts` 导出，SSE 与 REST 共用。

## 4. EventType → store 分发表

分发函数放 `api/dispatch.ts`（`dispatchEvent`），App 层 `useSSE(dispatchEvent)` 绑定，按 `e.event` 路由。核心先行的映射：

| EventType（`e.event`） | 路由到 | action | 说明 |
|---|---|---|---|
| `speak` | `chatStore` | `addSpeak` | 聊天区 Nyx 回复 |
| `ask` | `chatStore` | `addAsk` | 聊天区 Nyx 问句 |
| `think` | `chatStore` | `addThink` | 内心话（灰色斜体逐字展示） |
| `mutter` | `chatStore` + `announceStore` | `addMutter` + `announce("mutter", …)` | 碎碎念（聊天记录气泡 + 头像旁淡出气泡） |
| `initiate_chat` | `chatStore` | `addInitiateChat` | 搭话 |
| `emotion_update` | `innerLifeStore` | `updateEmotion` + `refreshState()` | 更新 valence/arousal/emotion（增量）+ 重拉全量快照带新能量/性格/三观（载荷只带情绪，不随帧下发） |
| `user_message` | `chatStore` | `addUserMessage` | 用户消息回显（发消息后 SSE 回播） |
| `desire_generated`/`desire_satisfied`/`desire_expired` | `desireStore` | `refresh()` | 欲望变化 → 重拉快照（事件只带 `desire_id`） |
| `activity_start`/`activity_interrupted` | `activityStore` | `refresh()` | 活动开始/抢占 → 重拉快照（事件只带 `activity_id`） |
| `activity_end` | `activityStore` + `announceStore` | `refresh()` 后按 `activity_id` 找产出并 `announce("activity", …)` | 活动完成 → 重拉快照 + 冒一句产出 |
| `exploration_step` | `explorationStore` | `onStep` | 点亮地图实时节点：`liveNodes` 追加帧 `node`、`activityId` 置为帧 `activity_id` |
| `encounter_start` | `encounterStore` | `onStart` | 遭遇开始 → 置 `current`（`EncounterCard` 渲染文本 + 可点选项） |
| `encounter_choice` | — | — | 无消费者（`encounter_end` 紧跟，由其清 `current` + 上屏 ending） |
| `encounter_end` | `encounterStore` | `onEnd` | 遭遇结束 → 清 `current` + ending 上聊天时间线 + 重拉内在/欲望/记忆快照 |
| `memory_created`/`memory_promoted` | `memoryStore` | `refresh()` | 记忆变化 → 重拉快照（事件只带 `memory_id`） |
| `reflection_done` | `desireStore` + `narrativeStore` + `announceStore` | `refresh()` +（`story_is_new` 时）`setHighlightedStory` / `announce("mutter", …)` + `refresh()` | 反思完成 → 欲望/叙事重拉快照 + 新故事高亮（叙事面板定位闪烁）+ 头像旁冒一句 |
| `clock_tick`/`observation_state`/`reflection` | — | — | 无消费者 |

> 完整 19 类见 `01-types.md` 的 `EventType`。`switch` 按类型路由：文本/情绪事件走 chatStore/innerLifeStore，`desire_*`/`activity_*`/`memory_*`/`reflection_done` 触发对应快照 store 的 `refresh()`（fire-and-forget）；`mutter` 额外进 `announceStore` 冒头像旁气泡、`activity_end` 完成后按 `activity_id` 找产出冒一句（`announceStore`）、`reflection_done` 的 `story_is_new=true` 额外 `setHighlightedStory` + `announce("mutter", …)` 高亮新故事，`clock_tick`/`observation_state`/`reflection` 无消费者（故无 `default` 分支）。
> **前向兼容边界**：命名事件（带 `event:` 行）若没有匹配的 `addEventListener` 且无 `onmessage`，浏览器会静默丢弃——故后端**新增 EventType 必须同步前端** `EVENT_TYPES` 数组 + `types/api.ts` 判别联合 + 本分发表（monorepo 内本就在同一提交改）。不存在「旧前端自动接住新类型」的兜底。

### 4.1 分发函数（含类型收窄）

```typescript
// api/dispatch.ts —— 事件 → store 路由
function dispatchEvent(e: SseEvent): void {
  switch (e.event) {
    case "speak": return chatStore.addSpeak(e);
    case "ask": return chatStore.addAsk(e);
    case "think": return chatStore.addThink(e);
    case "mutter":
      chatStore.addMutter(e);
      announceStore.announce("mutter", e.content);
      return;
    case "initiate_chat": return chatStore.addInitiateChat(e);
    case "user_message": return chatStore.addUserMessage(e);
    case "emotion_update":
      innerLifeStore.updateEmotion(e);
      void innerLifeStore.refreshState(); // 能量/性格/三观不随帧下发，重拉全量
      return;
    case "memory_created": case "memory_promoted":
      memoryStore.refresh(); return;
    case "desire_generated": case "desire_satisfied": case "desire_expired":
      desireStore.refresh(); return;
    case "activity_start": case "activity_interrupted":
      activityStore.refresh(); return;
    case "activity_end":
      void activityStore.refresh().then(() => {
        const id = e.activity_id;
        if (typeof id !== "string") return;
        const a = activityStore.getState().data?.schedule.find((x) => x.id === id);
        if (a === undefined) return;
        const text = activityAnnouncement(a);
        if (text !== null) announceStore.announce("activity", text);
      });
      return;
    case "exploration_step":
      explorationStore.onStep(e);
      return;
    case "encounter_start":
      encounterStore.onStart(e);
      return;
    case "encounter_choice":
      // 无消费者：encounter_end 紧跟其后，由它清 current + 上屏 ending
      return;
    case "encounter_end":
      encounterStore.onEnd(e);
      return;
    case "reflection_done":
      void desireStore.refresh();
      if (e.story_is_new) {
        narrativeStore.setHighlightedStory(e.story);
        const preview = e.story.length > 30 ? `${e.story.slice(0, 30)}…` : e.story;
        announceStore.announce("mutter", `小狐狸我呀，反思了一下：${preview}`);
      }
      void narrativeStore.refresh();
      return;
  }
}
```

- **编译期 + 运行期双层收窄**：`SseEvent` 是判别联合（§2），键名错位与「事件类型 → kind 标签」错位都在编译期即拦（`addSpeak` 入参 pin 成 `TextEvent<"speak">`，`e.content` 已是 `string`）；但 `useSSE` 对 wire JSON 做 `as unknown as SseEvent`（信任边界），故 store action 内仍保留运行时 `typeof` 校验（`addUserMessage` 验 `e.message`、`addSpeak` 验 `e.content`、`updateEmotion` 验三字段且 emotion 走 `isEmotionCategory` 枚举收窄），类型错/缺字段 → `console.error` + 丢弃该帧（原则 5）。**不用裸 `as string`**。
- **action 入参统一为整个 `SseEvent`**（非单独 `content`），因为消息还要取 `event_id`/`correlation_id` 溯源。

### 4.2 store 最小签名（完整 state 形状见 `02-stores.md`）

```typescript
chatStore.addSpeak(e: TextEvent<"speak">): void            // e.content → ChatMessage{kind:"speak"}
chatStore.addAsk(e: TextEvent<"ask">): void                // kind:"ask"
chatStore.addThink(e: TextEvent<"think">): void            // kind:"think"
chatStore.addMutter(e: TextEvent<"mutter">): void          // kind:"mutter"
chatStore.addInitiateChat(e: TextEvent<"initiate_chat">): void // kind:"initiate_chat"
chatStore.addUserMessage(e: UserMessageEvent): void        // 读 e.message → {kind:"message", role:"user"}
innerLifeStore.updateEmotion(e: EmotionUpdateEvent): void  // 覆盖 current 的 valence/arousal/emotion（emotion 走 isEmotionCategory 收窄）
desireStore.refresh(): Promise<void>                       // desire_* → 重拉快照（事件只带 desire_id，fire-and-forget）
activityStore.refresh(): Promise<void>                     // activity_start/interrupted → 重拉快照；activity_end → refresh 后按 activity_id 找产出
memoryStore.refresh(): Promise<void>                       // memory_* → 重拉快照
narrativeStore.setHighlightedStory(story: string): void    // reflection_done 且 story_is_new → 高亮新故事（叙事面板定位+闪烁）
narrativeStore.refresh(): Promise<void>                    // reflection_done → 重拉快照
announceStore.announce(kind: "mutter" | "activity", text: string): void  // 头像旁临时气泡（mutter 4s / activity 7s 后自动 dismiss）
```

> 每个 store 的 state 形状（`ChatMessage` 含 `id`/`role`/`kind`/`content`/`correlation_id`/`preloaded?`，不存 `timestamp`——见 02-stores；`InnerLifeState`、四个快照 store）与 action 完整实现见 `02-stores.md`。本表只给签名，保证分发表能独立落地。

### 4.3 user_message 回显与发消息的关系

- **发消息不本地乐观渲染**：`sendMessage` 只 `POST /api/chat` 拿 `{event_id}`（用于置 `isReplying`），**不本地 append 用户消息**。
- 用户消息**唯一来源** = SSE `user_message` 回显（localhost 往返 ~10ms，视觉无延迟）。POST 返回的 `event_id` 与该 `user_message` 帧的 `event_id` 一致，但**不用于去重**——本地根本没提前渲染，天然无重复。
- 完整流程见 `03-chat-panel.md` §2。

## 5. 容错与边界

- **重连**：`EventSource` 原生重连；`onerror` 置 `connecting`，不手写退避（浏览器默认指数退避）。后端重启期间帧丢失，恢复后靠 `GET /api/state` 重新拉快照对齐（App 层在 `status === "open"` 时触发一次 `refreshState`）。
- **断线期间的快照**：`innerLifeStore` 的 `CurrentState` 以 `GET /api/state` 快照为准，`emotion_update` 做增量覆盖（valence/arousal/emotion）+ 顺带 `refreshState()` 重拉全量快照（带新能量/性格/三观）；`chatStore` 的历史消息靠 `GET /api/events/log?event_type=speak` 补（核心先行可暂缓，先只展示 SSE 实时的）。
- **顺序**：SSE 单连接、后端顺序广播（05-event「顺序分发」），前端按到达顺序 append，不额外排序。
- **测试**（`tests/sse.test.ts`）：mock `EventSource`（fake 触发 `onopen`/`onmessage`/`onerror`）→ 断言 `dispatch` 收到解析正确的 `SseEvent`、`status` 三态切换、cleanup 调 `close()`、坏 `data` 帧被跳过不崩。

## 6. App 组合装配（`App.tsx`）

散落在 §3/§4 的胶水在此拼齐（核心先行的 App 最小骨架 + 视觉改造布局）：

```tsx
// App.tsx
function App() {
  const status = useSSE(dispatchEvent);          // dispatchEvent 来自 api/dispatch.ts（§4.1）
  const refreshState = useInnerLifeStore(s => s.refreshState);
  usePresence();                                 // 活跃度上报（README §2）
  const [view, setView] = useState<"chat" | "settings">("chat");  // 双模式：对话框 / 设置
  const [innerOpen, setInnerOpen] = useState(false);              // 内心世界抽屉开合
  const tint = useSettingsStore(s => s.tint);    // 背景色调/背景图（settingsStore）
  const image = useSettingsStore(s => s.image);

  // SSE 恢复连接后重拉快照（断线期间 emotion_update 可能丢失）
  useEffect(() => {
    if (status === "open") refreshState();
  }, [status, refreshState]);

  return (
    <div className="app">
      <div className="app-bg" aria-hidden="true" style={bgStyle} />  {/* 背景：有图 cover 铺底，无图有色纯色，图+色叠 .app-bg-tint */}
      {tint !== null && image !== null && (
        <div className="app-bg-tint" aria-hidden="true" style={{ backgroundColor: tint }} />
      )}
      <Sakura />                                       {/* 樱花飘落装饰（components/scene） */}
      <header className="app-topbar">
        <span className="scene-title">✦ Nyx ✦</span>
        <div className="topbar-right">
          <span className="connection-state">{CONNECTION_LABEL[status]}</span>
        </div>
      </header>
      <main className="app-stage">
        <EmotionSprite size="portrait" />              {/* 方形头像（左侧） */}
        {view === "chat" ? (
          <ChatPanel onOpenSettings={() => setView("settings")} onToggleInner={() => setInnerOpen(v => !v)} />  {/* 头部「内心」滑出抽屉 /「设置」进设置 */}
        ) : (
          <SidePanel onBack={() => setView("chat")} />              {/* 设置面板（背景/Eval），「返回对话」退出 */}
        )}
      </main>
      <InnerWorld open={innerOpen} onClose={() => setInnerOpen(false)} />  {/* 内心世界右侧滑出抽屉（默认收起） */}
    </div>
  );
}
```

- `useSSE` 只挂一次（App 顶层），子面板**不重复订阅**，只读 store。
- **视觉改造布局（Galgame）**：全屏三层——背景柔光 + 樱花（`app-bg`/`Sakura`）→ 左侧方形头像（`app-stage` + `EmotionSprite size="portrait"`，常驻）+ 右侧双模式区（对话框 ↔ 设置，互斥渲染）：默认微信式聊天窗（`ChatPanel` 的 `dialog-box`，头部「内心」按钮滑出右侧 `InnerWorld` 抽屉、「设置」按钮切走），点「设置」后换成 `SidePanel` 标签页（`components/layout`，背景/Eval 2 标签，一次显示一个，内容区可滚动，「返回对话」回聊天）。`InnerWorld` 抽屉收内在/欲望/活动/产出/叙事/资料/记忆 7 标签，默认收起。
- `connectionState` 由 App 层在顶栏 `connection-state` 直接显示，不再传 `ChatPanel`。
- 背景由 `settingsStore` 驱动：`image` 以 `cover` 铺底、`tint` 无图时作纯色替默认粉渐变、图+色并存时叠一层半透明 `.app-bg-tint`。
