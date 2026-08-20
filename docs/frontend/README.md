# 前端（Nyx Agent）

> React 前端，跑在 Tauri 薄壳里，通过 localhost HTTP/SSE 连接 Python 核心服务。
> 本文档集是前端的**设计/规划**：技术栈、目录结构、store 划分、SSE 数据流、核心面板组件契约。实现细节在编码阶段补全（对齐后端「spec 定义契约 → 实现照抄」的分工）。
> 范围：聊天面板 + 内在状态面板 + SSE 数据流 + 面板骨架，以及后续补齐的欲望/活动/记忆/eval/溯源五个面板（均已落地）。

## 1. 技术栈

| 层 | 选型 | 依据 |
|---|---|---|
| 语言 | TypeScript（`strict: true`） | CLAUDE.md 前端规范 |
| UI | React 18（函数组件 + hooks） | CLAUDE.md |
| 状态 | Zustand（每系统一个 store） | CLAUDE.md |
| 构建 | Vite | React 常规工具链 |
| 测试 | Vitest + React Testing Library | Vite 生态，同构 TS |
| 桌面壳 | Tauri（薄壳：**核心先行仅承载 webview**） | design §2 |
| 通信 | SSE over localhost（实时）+ REST（快照） | design §2 / tech-ref §4 |

> **核心先行范围外（defer）**：Tauri 壳的**托盘菜单、系统通知、自定义窗口行为**不在核心先行内。webview 层（聊天 + 内在 + SSE）跑通后，系统通知的**触发 UX 未定义**——搭话（`initiate_chat`）虽是核心先行事件、欲望满足（`desire_satisfied`）非核心先行，但两者「是否/何时弹系统通知」都未定义，届时再定，不在现在预埋触发点（反冗余）。

## 2. 进程边界

```
┌─────────────────────── Tauri 桌面进程 ───────────────────────┐
│  webview（React 前端）                                         │
│    ├─ REST：初始快照 / 发消息 / 历史查询 / 导出                │
│    └─ SSE：订阅全部事件（实时）                                │
└──────────────▲──────────────────────────┬───────────────────┘
               │ localhost HTTP / SSE     │（前端 Tauri 采集活跃度+窗口标题）
┌──────────────┴──────────────────────────▼───────────────────┐
│  Python 核心服务（uvicorn，独立本地进程）                     │
│    18 个 spec 实现的 Facade + EventBus + LangGraph            │
└─────────────────────────────────────────────────────────────┘
```

- Python 核心作为**独立本地服务**运行（`uvicorn`），Tauri 壳 + React 前端通过 localhost HTTP/SSE 连接；开发时手动起服务，不打包 sidecar（design §2）。
- 前端 Tauri 采集键盘/鼠标活跃度 + 窗口标题 → `classify_presence` 判定 → `POST /api/observe`（18-api 下游约定）。这是核心先行里唯一由前端发起的**被动上报**。

### 活跃度上报（`hooks/usePresence.ts`，核心先行唯一被动上报）

- **落点**：`hooks/usePresence.ts`，App 层挂载一次（与 `useSSE` 并列，见 01-sse §6），无 store（纯「采集 → 判定 → 上报」，不上屏，结果存后端 `last_presence`）。
- **判定镜像后端**（14-activity `observe.py`，规则逐字一致，不另造）：
  ```typescript
  type Presence = "online" | "away" | "busy";
  function classifyPresence(keyboardActive: boolean, mouseActive: boolean, windowTitle: string): Presence {
    if (keyboardActive || mouseActive) return "online";
    if (windowTitle) return "busy";
    return "away";
  }
  ```
- **活跃窗口降采样**：`keydown`/`mousemove` 监听更新 `last_key_ts`/`last_mouse_ts`；采样时刻算 `keyboardActive = now - last_key_ts < ACTIVE_WINDOW_SEC`、`mouseActive = now - last_mouse_ts < ACTIVE_WINDOW_SEC`（`ACTIVE_WINDOW_SEC = 30`）。
- **窗口标题**：Tauri `getCurrentWindow().title()`；核心先行可先恒传 `""`（→ 无输入时恒走 `away` 分支），真实采集后续补。
- **节奏**：每 `OBSERVE_INTERVAL_SEC = 30` 采样一次，**presence 变化才 `client.postObserve(presence)`**（→ `POST /api/observe`；不变不上报，避免每 tick 打后端）；首次挂载必报一次（后端 `last_presence` 初始 `"away"`，前端真实值要对齐）。

## 3. 数据流（双通道）

| 通道 | 用途 | 端点 |
|---|---|---|
| REST | 初始快照 + 发消息 + 历史查询 + 导出 | 11 个端点（tech-ref §4） |
| SSE | 实时**全部事件** | `GET /api/events` |

- **SSE 是主通道**：后端广播全部 18 类事件（design §3.2），前端按 `event` 类型增量更新面板；REST 只做进页面时的初始快照 + 用户主动动作（发消息/导出）。
- SSE `data` 统一形状（tech-ref §4）：

```json
{"event_id": "…", "correlation_id": "…", **event.content}
```

- 核心先行用到的端点：`GET /api/state`（`CurrentState` 快照）、`POST /api/chat`（发消息，返回 `{event_id}`，回复走 SSE）、`POST /api/observe`（活跃度上报 `{presence}`，返回 `{event_id}`，见 §2）、`GET /api/events`（SSE）。

## 4. 目录结构

```
frontend/
  package.json
  vite.config.ts
  tsconfig.json              # strict: true
  index.html
  src/
    main.tsx                 # React 入口，挂载 App
    App.tsx                  # 全屏装配：背景(可调色/传图)/樱花 + 左侧半身像立绘 + 「对话框/设置」双模式切换（01-sse §6）
    types/
      api.ts                 # 后端契约 TS 镜像（Event/CurrentState/EmotionCategory/…）
    lib/
      labels.ts              # 枚举值→中文 UI 标签（label() 未知键回退原值）
    api/
      client.ts              # REST fetch 封装（postChat / getState / postObserve，见 05-client）
      dispatch.ts            # SseEvent → store 路由（01-sse §4.1）
    hooks/
      useSSE.ts              # SSE 订阅 hook（CLAUDE.md 点名）
      usePresence.ts         # 活跃度采集 + classifyPresence 判定 + POST /api/observe
      useTypewriter.ts       # 打字机：nyx 文本逐字显示（纯渲染层，03-chat-panel）
    stores/
      chatStore.ts           # 聊天：消息列表 + 历史加载
      innerLifeStore.ts      # 内在状态：CurrentState 快照
      eventStore.ts          # 溯源时间线（SSE 全量 + /api/events/log 历史）
      desireStore.ts         # 欲望：DesireState 快照
      activityStore.ts       # 活动：ActivitySnapshot 快照
      memoryStore.ts         # 记忆：Memory[] 快照
      evalStore.ts           # eval + token：reports/tokens 快照
      narrativeStore.ts      # 自我叙事：SelfNarrative 快照
      materialsStore.ts      # 资料上传：文件清单 + 上传动作
      settingsStore.ts       # 背景外观：tint/image（纯前端 UI 状态，无后端）
    components/
      chat/
        ChatPanel.tsx
        MessageList.tsx
        MessageBubble.tsx
        ChatInput.tsx
      inner/
        InnerStatePanel.tsx
        ValenceArousalPlot.tsx
        EmotionSprite.tsx
        EnergyBar.tsx
        BigFiveChart.tsx
        ValuesChart.tsx
      panels/
        BackgroundPanel.tsx # 背景外观（预设色调/自定义取色/上传背景图/恢复默认）
        DesiresPanel.tsx     # 欲望面板（GET /api/desires + SSE desire_*）
        ActivityPanel.tsx    # 活动时间线（GET /api/activity + SSE activity_*）
        NarrativePanel.tsx   # 自我叙事（GET /api/narrative）
        MaterialsPanel.tsx   # 资料上传（POST /api/upload + GET /api/materials）
        MemoryPanel.tsx      # 记忆浏览器（GET /api/memories + SSE memory_*）
        EvalPanel.tsx        # eval + token 看板（GET /api/eval / GET /api/tokens）
        TracePanel.tsx       # 事件溯源时间线（SSE 全部 + GET /api/events/log）
      layout/
        Panel.tsx            # 通用面板容器
        SidePanel.tsx        # 设置标签页面板（收非对话面板，标签切换，01-sse §6）
      scene/
        Sakura.tsx           # 樱花飘落装饰（纯视觉，01-sse §6）
    assets/
      sprites/               # 8 情绪 sprite（EmotionCategory 1:1）
  tests/
    api.test.ts              # API 端点测试（CLAUDE.md 要求）
    sse.test.ts
    stores.test.ts
    labels.test.ts           # 枚举中文化映射 + label() 回退
```

### 命名约定（对齐 CLAUDE.md 前端规范）

- 组件 `PascalCase`、文件 `camelCase.tsx`；store/hook/api 文件 `camelCase.ts`。
- **TS 类型字段名 = 后端 JSON 键名（snake_case 原样，零映射）**：后端 dataclass 直接 `json.dumps`，字段是 `snake_case`（`valence` / `energy_state` / `current_activity` / `correlation_id`）。前端类型镜像时**不改名**，理由：零映射层 = 零映射 bug、字段名可沿 `correlation_id` 一路溯源到后端定义（原则 3/5）。前端内部 UI 变量名才用 camelCase，落类型时转 snake_case 键。

## 5. 面板骨架（SidePanel 标签页）

design §11 列 7 个面板，全部落地。视觉改造后，聊天区独立为中间微信式大窗（`ChatPanel`），其头部「设置」按钮切到 `SidePanel` 标签页（背景/内在/欲望/活动/叙事/资料/记忆/Eval/溯源 9 标签，一次显示一个，内容区可滚动，「返回对话」回到聊天）。枚举值一律经 `lib/labels.ts` 转中文上屏（如 `exploration → 发现`），未知键回退原值。

| 面板 | 状态 | 数据源 | 组件落点 |
|---|---|---|---|
| 聊天区 | ✅ 实现（03-chat-panel） | `POST /api/chat` + SSE `speak`/`think`/`ask` | `components/chat/ChatPanel.tsx` |
| 背景外观 | ✅ 实现 | 无（纯前端 `settingsStore`） | `components/panels/BackgroundPanel.tsx` |
| 内在状态面板 | ✅ 实现（04-inner-state-panel） | `GET /api/state` + SSE `emotion_update` | `components/inner/InnerStatePanel.tsx` |
| 欲望面板 | ✅ 实现 | `GET /api/desires` + SSE `desire_*` | `components/panels/DesiresPanel.tsx` |
| 活动时间线 | ✅ 实现 | `GET /api/activity` + SSE `activity_*` | `components/panels/ActivityPanel.tsx` |
| 自我叙事 | ✅ 实现 | `GET /api/narrative` | `components/panels/NarrativePanel.tsx` |
| 资料上传 | ✅ 实现 | `POST /api/upload` + `GET /api/materials` | `components/panels/MaterialsPanel.tsx` |
| 记忆浏览器 | ✅ 实现 | `GET /api/memories` + SSE `memory_*` | `components/panels/MemoryPanel.tsx` |
| eval + token 看板 | ✅ 实现 | `GET /api/eval` / `GET /api/tokens` | `components/panels/EvalPanel.tsx` |
| 事件溯源时间线 | ✅ 实现 | SSE 全部 + `GET /api/events/log` | `components/panels/TracePanel.tsx` |

## 6. 测试约定

- CLAUDE.md：「所有 API 端点必须有测试」→ `tests/api.test.ts` 覆盖 `client.ts` 的每个端点（mock fetch，断言请求 URL/方法/响应解析）。
- SSE：`tests/sse.test.ts` 用 mock `EventSource` 验证 `useSSE` 的事件解析 + 分发给 store。
- stores：`tests/stores.test.ts` 验证每个 store 的 action 纯逻辑（不依赖真实网络）。
- 测试不依赖真实后端/真实桌面；验证管道正确性（事件走对 store、类型解析正确），不验证视觉样式。

## 7. 文档索引

| 文件 | 覆盖 |
|---|---|
| `README.md` | 本文（总览） |
| `01-sse.md` | `useSSE` hook、EventSource 对接、data 形状、EventType→store 分发表、重连容错 |
| `02-stores.md` | `chatStore` / `innerLifeStore` / `eventStore` 的 state 形状 + actions |
| `03-chat-panel.md` | 聊天面板组件树、发消息流程、speak/think/ask 渲染 |
| `04-inner-state-panel.md` | 内在状态面板组件树、valence-arousal 图、精力条、情绪 sprite、Big Five/三观 |
| `05-client.md` | `client.ts` 薄 fetch 封装（postChat/getState/postObserve）+ 错误契约 |
