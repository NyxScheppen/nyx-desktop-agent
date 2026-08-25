# 聊天面板（`components/chat/`）

> 核心面板之一：消息列表 + 输入框。用户发消息 → `POST /api/chat` → SSE 回显 + `speak`/`think`/`ask` 上屏。
> 范围：`components/chat/{MessageList,MessageBubble,ChatInput}.tsx` 的组件树、发消息流程、Nyx 产出渲染（含 `encounter` 遭遇结局）。
>
> **`ChatPanel` 已拆散（06-game-shell）**：容器职责迁到三区布局——`MessageList` → 书卷区对话模式（`ScrollArea`）、`ChatInput` → Galgame 对话框、头部「内在/空间/记录」按钮 → 左面板摘要点击（`LeftPanel`）。本 spec 保留 `MessageList`/`MessageBubble`/`ChatInput` 的组件契约（复用不重写），不再描述 `ChatPanel` 容器。

## 1. 组件树

```
（`ChatPanel` 容器已拆散，职责迁到 06-game-shell；以下为保留复用的组件）
MessageList                # 微信式全量列表：全部消息按序渲染，最新滚到底，上滑看历史（滚动条隐藏）——现挂书卷区对话模式（ScrollArea）
└─ MessageBubble           # 单条：按 role/kind 渲染，nyx 文本走 useTypewriter 逐字（见 §3）
ChatInput                  # 输入框 + 发送按钮；isReplying 时仅禁用发送按钮（输入框可预打下一句）——现为 Galgame 对话框
└─ sendError               # 红字，挂在 ChatInput 下方，读 chatStore.sendError
```

- `ChatPanel`：`useChatStore(s => s.messages)` 订阅；`useSSE` 不在此（SSE 挂 App 层，见 01-sse §4，`ChatPanel` 只消费 store）。
- 连接状态显示：`ConnectionState` 由 App 层在顶栏 `connection-state` 直接显示，不再传 `ChatPanel`（01-sse §6）。

## 2. 发消息流程

```
用户输入 → 点发送
  → chatStore.sendMessage(text)
      ├─ POST /api/chat {message: text}   （client.ts）
      ├─ 成功：isReplying = true（按钮转「…」，输入框清空）
      └─ 失败：sendError = "…"（红字挂在 ChatInput 下方，下次 sendMessage 成功自动清空，不阻断重试）
  → 后端 publish USER_MESSAGE → SSE user_message 回显 → chatStore.addUserMessage（上屏）
  → 后端 ExpressionFacade.reply() 产 speak/think/ask → SSE → chatStore.addSpeak/addThink/addAsk（上屏）
  → addSpeak/addAsk 置 isReplying=false（回复交付，按钮恢复）
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
| `nyx` | `mutter` | 左气泡，斜体/浅色 | 碎碎念（自发，无用户触发） |
| `nyx` | `initiate_chat` | 左气泡，带「欲望搭话」徽标 | 主动搭话（与 mutter 区分来源） |
| `nyx` | `encounter` | 左气泡，带「遭遇」徽标 | 遭遇结局叙事（即时全量，不逐字） |

- **打字机（`useTypewriter`）**：nyx 文本消息（`speak`/`ask`/`think`/`mutter`/`initiate_chat`）逐字显示，纯渲染层 hook（`hooks/useTypewriter.ts`），不改 store——消息仍完整 append，仅控制「显示到第几个字」；未打完时挂 `.cursor-blink` 光标。`useTypewriter(text, speed, ready)` 加第三参 `ready`：false 时不启动（`displayed=""`、`done=false`、无光标），转 true 才从 0 逐字。
- **微信式全量 + 全串行逐字（视觉改造 §4）**：`MessageList` 全部消息按序渲染，每条非 `preloaded` 的 nyx 文本消息都逐字（`MessageBubble` 内部 `isNyxText && !preloaded` 判定走 `useTypewriter`），用户消息即时全量；每条消息不打完也已在 DOM；后端 SSE 顺序 THINK 先于 SPEAK（17-expression），故「内心话气泡」天然排在「发言气泡」之上；随内容增长同步滚到底——`MessageList` 用 `MutationObserver` 观察滚动容器自身 DOM 变化（新消息 `childList` + 打字机逐字 `characterData` 都触发），但仅当用户已在底部才跟随（上滑看历史不被逐字拉回底，回到底部恢复跟随）；故打字过程中页面跟着她的话往下滚（滚动条隐藏）。
- **串行逐字（内心话 → 对话，不并发）**：`MessageList` 对每条消息算 `ready = isReady(message, index, messages, typedIds)`（纯函数，导出供测试）——每条 nyx 文本消息需等「同 `correlation_id` 且在其之前的所有 nyx 文本消息」都已入 `typedIds` 才就绪；逐字 `done` 时经 `onTyped → markTyped` 写入 `typedIds`。故内心话气泡先完整逐字打完，对话气泡才开始逐字（等待期 `displayed=""`、无光标），而非两条并发一起显示。`preloaded` 历史消息与用户消息恒就绪。

## 4. 边界

- **历史加载（`ChatPanel` 挂载时 `loadHistory()`）**：进页面并行 `GET /api/events/log`（`user_message`/`speak`/`ask`/`think`/`mutter`/`initiate_chat` 六类，各 `limit=200`）回填历史消息，`preloaded:true`（渲染时不逐字、直接全量上屏），按 `timestamp` 升序前置到现有消息前、按 `id` 去重（跳过已存在的）；历史 think 一并入 `typedIds` 视为已打完，不阻塞实时 speak/ask。重启后消息列表不再空。
- **`mutter`/`initiate_chat` 无用户消息对齐**：它们 `correlation_id` 指向 MUTTER_CHECK tick / desire，不在用户消息链上——渲染按到达顺序插在列表里，不强行对齐到某条用户消息。
- **长文本**：气泡 `max-width` + 自动换行；`think` 逐字弱化展示（灰色斜体小字），不再折叠。

## 5. 测试（`tests/` 并入 stores/api 测试）

- `MessageBubble`：按 `kind` 渲染正确（`speak` 正常 / `think` 灰色斜体逐字 / `ask` 高亮）；nyx 文本消息须先 `advanceTimersByTime`（fake timers）打完字再断言完整文案——React Testing Library 断言关键 class/文案。
- `MessageList`：全部消息按序渲染、无历史折叠（`typeDone` 推进 fake timers 后两条都上屏，无历史按钮）；全部气泡渲染即存在（串行门控只延迟内容不延迟挂载）；串行逐字：内心话先打完、对话才开打（未推进 timer 两者皆空，`typeDone` 后串行完整上屏）；`isReady` 全串行逐字门控纯函数在 stores.test.ts 覆盖。
- `ChatInput`：`isReplying=true` 禁用发送；回车/点发送触发 `sendMessage`（mock store action）。
- 视觉样式不做断言（README §6 测试约定）。
