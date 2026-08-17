# 聊天面板（`components/chat/`）

> 核心面板之一：消息列表 + 输入框。用户发消息 → `POST /api/chat` → SSE 回显 + `speak`/`think`/`ask` 上屏。
> 范围：`components/chat/{ChatPanel,MessageList,MessageBubble,ChatInput}.tsx` 的组件树、发消息流程、5 类 Nyx 产出渲染。

## 1. 组件树

```
ChatPanel                     # 面板容器（layout/Panel 包裹）
├─ MessageList                # 滚动列表，读 chatStore.messages，自动滚到底
│   └─ MessageBubble          # 单条：按 role/kind 渲染（见 §3）
└─ ChatInput                  # 输入框 + 发送按钮；isReplying 时仅禁用发送按钮（输入框可预打下一句）
    └─ sendError              # 红字，挂在 ChatInput 下方，读 chatStore.sendError
```

- `ChatPanel`：`useChatStore(s => s.messages)` 订阅；`useSSE` 不在此（SSE 挂 App 层，见 01-sse §4，`ChatPanel` 只消费 store）。
- 连接状态显示：`ConnectionState` 由 App 层传给 `ChatPanel`（右上角「已连接/重连中」，非本组件职责）。

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
| `nyx` | `think` | 灰色小字 / 可折叠 | 内心话，弱化展示（默认折叠，点开看） |
| `nyx` | `mutter` | 左气泡，斜体/浅色 | 碎碎念（自发，无用户触发） |
| `nyx` | `initiate_chat` | 左气泡，带「搭话」标记 | 主动搭话（与 mutter 区分来源） |

- **sprite**：`speak`/`ask`/`initiate_chat` 气泡内或旁侧挂当前情绪 sprite（读 `innerLifeStore.current?.emotion` → `assets/sprites/` 对应图；`current === null`（快照未回）时不挂 sprite、不崩——SSE 消息可能早于快照到达），让回复带表情（04-inner-state-panel 有 sprite 表）。
- **头像/sprite 懒加载**：`emotion` 变化只重渲染 sprite，不重渲染整条列表。

## 4. 边界

- **无历史加载**（核心先行）：消息列表只展示本次会话 SSE 实时到达的消息；进页面历史靠 `GET /api/events/log?event_type=speak` 补是后续（README §5 溯源面板/历史查询）。
- **`mutter`/`initiate_chat` 无用户消息对齐**：它们 `correlation_id` 指向 MUTTER_CHECK tick / desire，不在用户消息链上——渲染按到达顺序插在列表里，不强行对齐到某条用户消息。
- **长文本**：气泡 `max-width` + 自动换行；`think` 折叠默认收起，避免刷屏。

## 5. 测试（`tests/` 并入 stores/api 测试）

- `MessageBubble`：按 `kind` 渲染正确（`speak` 正常 / `think` 灰色折叠 / `ask` 高亮）——React Testing Library 断言关键 class/文案。
- `ChatInput`：`isReplying=true` 禁用发送；回车/点发送触发 `sendMessage`（mock store action）。
- 视觉样式不做断言（README §6 测试约定）。
