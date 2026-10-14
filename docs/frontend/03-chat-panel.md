# 聊天面板（`components/chat/`）

> 核心面板之一：消息列表 + 输入框。用户发消息 → `POST /api/chat` → SSE 回显 + `speak`/`think`/`ask` 上屏。
> 范围：`components/chat/{MessageList,MessageBubble,ChatInput}.tsx` 的组件树、发消息流程、Nyx 产出渲染，以及 `lib/time.ts` 的共享时间纯函数。
>
> **`ChatPanel` 已拆散**：容器职责迁到 App 精简装配——`MessageList` → 左栏常驻对话（`div.left-dock`）、`ChatInput` → 左栏底部输入框。本 spec 保留 `MessageList`/`MessageBubble`/`ChatInput` 的组件契约（复用不重写），不再描述 `ChatPanel` 容器。

## 1. 组件树

```
（`ChatPanel` 容器已拆散，以下为保留复用的组件）
MessageList                # 微信式全量列表：全部消息按序渲染，最新滚到底，上滑看历史（滚动条隐藏）——现挂左栏（div.left-dock，flex:1 滚动）
├─ TimeDivider             # 首条、跨自然日或跨会话间隔时显示后端事件时间
└─ MessageBubble           # 单条：按 role/kind 渲染，nyx 文本走 useTypewriter 逐字（见 §3）
ChatInput                  # 输入框 + 发送按钮；isReplying 时仅禁用发送按钮（输入框可预打下一句）——现挂左栏底部
└─ sendError               # 红字，挂在 ChatInput 下方，读 chatStore.sendError
```

- `ChatPanel`：`useChatStore(s => s.messages)` 订阅；`useSSE` 不在此（SSE 挂 App 层，见 01-sse §4，`ChatPanel` 只消费 store）。
- 连接状态显示：`ConnectionState` 由 App 层在顶栏 `connection-state` 直接显示，不再传 `ChatPanel`（01-sse §6）。

## 2. 发消息流程

```
用户输入 → 点发送
  → chatStore.sendMessage(text)
      ├─ POST /api/chat {message: text, reply_to?: attempt_id}   （client.ts）
      ├─ 成功：isReplying = true（按钮转「…」，输入框清空）
      └─ 失败：sendError = "…"（红字挂在 ChatInput 下方，下次 sendMessage 成功自动清空，不阻断重试）
  → 后端 publish USER_MESSAGE → SSE user_message 回显 → chatStore.addUserMessage（上屏）
  → 后端 ExpressionFacade.reply() 产 speak/think/ask → SSE → chatStore.addSpeak/addThink/addAsk（上屏）
  → addSpeak/addAsk 置 isReplying=false（回复交付，按钮恢复）

提问气泡按 `attempt_id` 合并 ASK 与 READING_QUESTION；只有一个等待交互时自动绑定，多个等待项
要求用户先点选目标。气泡显示服务端校正后的剩余时间，并可暂停/继续；用户发送成功后目标 attempt
进入 `claimed`，不再作为下一条消息的候选。

主动搭话使用同一等待 attempt：实时事件携带 `attempt_id`/`expires_at`，气泡显示服务端校正后的
“剩余 N 秒”，到零后显示“等待超时确认”。主动搭话只显示计时，不增加暂停/继续按钮。
```

- **去重**：无——SSE 是消息唯一来源，`sendMessage` 不本地 append（02-stores §1 决策），故无「乐观 + 回显」重复问题。
- **超时兜底**：`sendMessage` 后 60s 未收到 `speak`/`ask` → 置 `isReplying=false` + `sendError="回复超时"`（LLM 卡死/后端异常时防转圈）。

## 3. `MessageBubble` 渲染（按 role/kind）

| role | kind | 渲染 | 说明 |
|---|---|---|---|
| `user` | `message` | 右对齐气泡，纯文本 | 用户消息 |
| `nyx` | `speak` | 左气泡，纯文本 | 主回复，正常展示 |
| `nyx` | `ask` | 左气泡，高亮/带问句样式 | 问句，等待用户回应 |
| `nyx` | `think` | 灰色斜体小字，逐字显示 | 内心话，弱化展示（useTypewriter 逐字，后端 THINK 先于 SPEAK 到达） |
| `nyx` | `initiate_chat` | 左气泡，带「欲望搭话」徽标 + 等待回应倒计时 | 主动搭话 |
| `nyx` | `reading_question` | 左气泡，带「提问」徽标 + 划线引文，即时全量 | 读书提问并进对话（不逐字，不进打字机） |

- **打字机（`useTypewriter`）**：nyx 文本消息（`speak`/`ask`/`think`/`initiate_chat`，即 `isNyxText` 白名单）逐字显示，纯渲染层 hook（`hooks/useTypewriter.ts`），不改 store——消息仍完整 append，仅控制「显示到第几个字」；未打完时挂 `.cursor-blink` 光标。`useTypewriter(text, speed, ready)` 加第三参 `ready`：false 时不启动（`displayed=""`、`done=false`、无光标），转 true 才从 0 逐字。**reading 两 kind 不进 `isNyxText`/`NYX_TEXT_KINDS` 白名单**：即时全量渲染、不进打字机串行门。
- **微信式全量 + 全串行逐字（视觉改造 §4）**：`MessageList` 全部消息按序渲染，每条非 `preloaded`、且本角色本次前端会话尚未显示过的 nyx 文本消息逐字（`MessageBubble` 按 event ID 查询 `seenIds`）；用户消息与读书 turn 即时全量。消息正文首次 `ready`、实际开始显示时记入 `seenIds`，本次挂载继续打完；再次挂载即使上次中途关闭也直接显示全文，并补记 `typedIds` 解锁后续同回合消息。`ready=false` 的等待气泡虽已挂载但不能提前记为已看；角色 `reset()` 清空记录。`seenIds` 不持久化，重新启动后 `loadHistory()` 回填的 `preloaded` 历史直接显示全文。后端 SSE 顺序 THINK 先于 SPEAK（11-expression），故「内心话气泡」天然排在「发言气泡」之上；随内容增长同步滚到底——`MessageList` 用 `MutationObserver` 观察滚动容器自身 DOM 变化（新消息 `childList` + 打字机逐字 `characterData` 都触发），但仅当用户已在底部才跟随（上滑看历史不被逐字拉回底，回到底部恢复跟随）。
- **串行逐字（内心话 → 对话，不并发）**：`MessageList` 对每条消息算 `ready = isReady(message, index, messages, typedIds)`（纯函数，导出供测试）——每条 nyx 文本消息需等「同 `correlation_id` 且在其之前的所有 nyx 文本消息」都已入 `typedIds` 才就绪；逐字 `done` 时经 `onTyped → markTyped` 写入 `typedIds`。故内心话气泡先完整逐字打完，对话气泡才开始逐字（等待期 `displayed=""`、无光标），而非两条并发一起显示。`preloaded` 历史消息与用户消息恒就绪。
- **明线时间分隔**：`shouldShowTimeDivider(current, previous)` 为纯函数。列表首条必显示；与上一条
  不同本地自然日时显示；同日相隔至少 30 分钟时显示；同一 `correlation_id` 内的
  THINK/SPEAK/ASK 即使生成稍慢也不重复分隔。标签使用事件 `timestamp` 转本地时间：今天为
  `今天 HH:mm`，昨天为 `昨天 HH:mm`，更早为 `M月D日 周X HH:mm`。时间分隔属于列表内容，
  同样参与现有滚动跟随，但不进入 store、不参与打字机。

## 4. 边界

- **共享时间纯函数**：`lib/time.ts` 不访问网络、不保存状态；`Date` 使用系统本地时区，消息
  timestamp 使用 epoch 秒。公开签名为：

  ```typescript
  export type TimePhase = "day" | "night";
  type TimedMessage = { kind: string; correlation_id: string; timestamp: number };
  export function timePhaseAt(value: Date): TimePhase;
  export function formatCurrentTime(value: Date): string;
  export function formatMessageTime(timestamp: number, now: Date): string;
  export function isValidTimestamp(value: unknown): value is number;
  export function shouldShowTimeDivider(
    current: TimedMessage, previous: TimedMessage | null,
  ): boolean;
  ```

  `timePhaseAt()` 夜间为本地 `22:00-06:00`；`formatCurrentTime()` 返回 `M月D日 星期X HH:mm`。
  消息标签与分隔规则见 §3。`MessageList` 接收 `messages: ChatMessage[]` 和可选 `now: Date`，
  isValidTimestamp 检查有限数值与 Date 范围；formatMessageTime 的非法输入返回“时间未知”。
  App 注入共享时钟；独立使用时才以当前本地时间作默认值，不新增时间 store。
- **历史加载（`loadHistory()`）**：进页面并行 `GET /api/events/log`（`user_message`/`speak`/`ask`/`think`/`initiate_chat`/`reading_question` 六类，各 `limit=5000`）回填历史消息，`preloaded:true`（不逐字），与已有消息合并后全局按 `timestamp` 升序、按 `id` 去重；非法 timestamp 丢弃。历史 think 一并入 `typedIds` 视为已打完，不阻塞实时 speak/ask。历史在 SSE 前后到达都不重复、不错序。
- **`initiate_chat`/读书 turn 无用户消息对齐**：它们 `correlation_id` 指向 desire tick / book_id，不在用户消息链上——渲染按到达顺序插在列表里，不强行对齐到某条用户消息。
- **长文本**：气泡 `max-width` + 自动换行；`think` 逐字弱化展示（灰色斜体小字），不再折叠。

## 5. 测试（`tests/` 并入 stores/api 测试）

- `MessageBubble`：按 `kind` 渲染正确（`speak` 正常 / `think` 灰色斜体逐字 / `ask` 高亮）；等待中的 `initiate_chat` 使用 fake system time 断言实时倒计时；首次逐字、播放完或中途关闭再挂载直接全文、未 ready 不记已看、角色 reset 隔离。
- `MessageList`：全部消息按序渲染、无历史折叠（`typeDone` 推进 fake timers 后两条都上屏，无历史按钮）；全部气泡渲染即存在（串行门控只延迟内容不延迟挂载）；串行逐字：内心话先打完、对话才开打；中途重新挂载时已开始的内心话直接完成、未开始的对话仍逐字；`isReady` 全串行逐字门控纯函数在 stores.test.ts 覆盖。
- 时间显示：fake system time 覆盖列表首条、同 correlation、同日 29:59/30:00、跨午夜、
  今天/昨天/更早标签；历史回填与实时消息在相同 epoch 下渲染完全一致。
  隔夜验收沿用 [表达契约](../specs/11-expression.md) 的 19:00→次日 08:00 场景：两组消息前
  分别显示对应时间，前端重启或重连回填后保留相同事件时间；不改变气泡宽度、串行逐字或
  滚动跟随行为。
- `ChatInput`：`isReplying=true` 禁用发送；回车/点发送触发 `sendMessage`（mock store action）。
- 视觉样式不做断言（README §6 测试约定）。
