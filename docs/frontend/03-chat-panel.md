# 聊天面板（`components/chat/`）

> 核心面板之一：消息列表 + 输入框。用户发消息 → `POST /api/chat` → SSE 回显 + `speak`/`think`/`ask` 上屏。
> 范围：`components/chat/{ChatPanel,MessageList,MessageBubble,ChatInput}.tsx` 的组件树、发消息流程、5 类 Nyx 产出渲染。

## 1. 组件树

```
ChatPanel                     # 底部对话框容器（dialog-box，Galgame 样式，非 layout/Panel）
├─ MessageList                # 串行打字：只显示当前一条，前一条打完推进，更早的收进「历史」
│   └─ MessageBubble          # 单条：按 role/kind 渲染，nyx 文本走 useTypewriter 逐字，done 回调 onDone（见 §3）
└─ ChatInput                  # 输入框 + 发送按钮；isReplying 时仅禁用发送按钮（输入框可预打下一句）
    └─ sendError              # 红字，挂在 ChatInput 下方，读 chatStore.sendError
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
| `nyx` | `speak` | 左气泡，Nyx 头像 + sprite | 主回复，正常展示 |
| `nyx` | `ask` | 左气泡，高亮/带问句样式 | 问句，等待用户回应 |
| `nyx` | `think` | 灰色斜体小字，逐字显示 | 内心话，弱化展示（useTypewriter 逐字，后端 THINK 先于 SPEAK 到达） |
| `nyx` | `mutter` | 左气泡，斜体/浅色 | 碎碎念（自发，无用户触发） |
| `nyx` | `initiate_chat` | 左气泡，带「搭话」标记 | 主动搭话（与 mutter 区分来源） |

- **sprite**：`speak`/`ask`/`initiate_chat` 气泡内或旁侧挂当前情绪 sprite（读 `innerLifeStore.current?.emotion` → `assets/sprites/` 对应图；`current === null`（快照未回）时不挂 sprite、不崩——SSE 消息可能早于快照到达），让回复带表情（04-inner-state-panel 有 sprite 表）。
- **头像/sprite 懒加载**：`emotion` 变化只重渲染 sprite，不重渲染整条列表。
- **打字机（`useTypewriter`）**：nyx 文本消息（`speak`/`ask`/`think`/`mutter`/`initiate_chat`）逐字显示，纯渲染层 hook（`hooks/useTypewriter.ts`），不改 store——消息仍完整 append，仅控制「显示到第几个字」；未打完时挂 `.cursor-blink` 光标。
- **串行打字 + 单条当前显示（视觉改造 §4）**：`MessageList` 维护「当前一条」索引，前一条 `done` 后停顿 350ms 才推进到下一条——后端 SSE 顺序 THINK 先于 SPEAK（17-expression），故天然「先内心话后发言」；更早的消息收进「历史（N）」，点开回看（历史气泡 `animate=false` 直接完整显示）；最后一条打完停留，等新消息到达再推进。

## 4. 边界

- **无历史加载**（核心先行）：消息列表只展示本次会话 SSE 实时到达的消息；进页面历史靠 `GET /api/events/log?event_type=speak` 补是后续（README §5 溯源面板/历史查询）。
- **`mutter`/`initiate_chat` 无用户消息对齐**：它们 `correlation_id` 指向 MUTTER_CHECK tick / desire，不在用户消息链上——渲染按到达顺序插在列表里，不强行对齐到某条用户消息。
- **长文本**：气泡 `max-width` + 自动换行；`think` 逐字弱化展示（灰色斜体小字），不再折叠。

## 5. 测试（`tests/` 并入 stores/api 测试）

- `MessageBubble`：按 `kind` 渲染正确（`speak` 正常 / `think` 灰色斜体逐字 / `ask` 高亮）；nyx 文本消息须先 `advanceTimersByTime`（fake timers）打完字再断言完整文案——React Testing Library 断言关键 class/文案。
- `MessageList`：只显示当前一条（`typeDone` 多轮推进 fake timers 后，最后一条停留、更早的折叠进「历史」）；串行——第一条未打完时后续不渲染。
- `ChatInput`：`isReplying=true` 禁用发送；回车/点发送触发 `sendMessage`（mock store action）。
- 视觉样式不做断言（README §6 测试约定）。
