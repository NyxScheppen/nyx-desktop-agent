# 前端（Nyx Agent）

> React 前端，跑在 Tauri 薄壳里，通过 localhost HTTP/SSE 连接 Python 核心服务。
> 本文档集是前端的**设计/规划**：技术栈、目录结构、store 划分、SSE 数据流、核心面板组件契约。实现细节在编码阶段补全（对齐后端「spec 定义契约 → 实现照抄」的分工）。
> 范围：聊天面板 + 内在状态面板 + SSE 数据流 + 面板骨架，以及后续补齐的欲望/活动/记忆/eval 四个面板（均已落地）。

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
| REST | 初始快照 + 发消息 + 历史查询 + 导出 | 20 个端点（tech-ref §4） |
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
      activityResult.ts      # 活动产出纯函数（activitySubject / formatResult / formatOutputBody / activityAnnouncement）
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
      desireStore.ts         # 欲望：DesireState 快照
      activityStore.ts       # 活动：ActivitySnapshot 快照 + 跨天产出 results
      memoryStore.ts         # 记忆：Memory[] 快照
      evalStore.ts           # eval + token：reports/tokens 快照
      narrativeStore.ts      # 自我叙事：SelfNarrative 快照
      materialsStore.ts      # 资料上传：文件清单 + 上传动作
      readingNotesStore.ts   # 读书笔记：笔记清单 + 删除动作（批注增删查走组件本地 state）
      encounterStore.ts      # 遭遇：未决遭遇 + 选择/结局（19-encounter）
      explorationStore.ts    # 探索：decision 决策载荷 + history 足迹 + autopilot 托管 + start/choose（逐层地牢，POST /api/explore + /api/explore/choose + /api/explore/autopilot）
      settingsStore.ts       # 背景外观：tint/image（纯前端 UI 状态，无后端）
      announceStore.ts       # 头像旁临时气泡：items/announce/dismiss（纯前端呈现，无后端）
    components/
      StatusBar.tsx          # 主界面常驻状态条（当前活动文字，App 层挂载）
      AnnounceLayer.tsx      # 头像旁淡出气泡层（读 announceStore，App 层挂载）
      chat/
        MessageList.tsx
        MessageBubble.tsx
        ChatInput.tsx
      encounter/
        EncounterCard.tsx    # 遭遇卡片（encounter_start 文本 + 可点选项，读 encounterStore）
      exploration/
        ExplorationMap.tsx   # 探索地牢（HUD + 本层 4 槽 + 下楼/撤退 + 展开地图 + 道具栏占位，读 explorationStore）
      inner/
        InnerStatePanel.tsx
        ValenceArousalPlot.tsx
        EmotionSprite.tsx
        Avatar.tsx           # 头像立绘：戳立绘/红点通知/昼夜节律（包裹 EmotionSprite）
        EnergyBar.tsx
        BigFiveChart.tsx
        ValuesChart.tsx
      panels/
        BackgroundPanel.tsx # 背景外观（预设色调/自定义取色/上传背景图/恢复默认）
        DesiresPanel.tsx     # 欲望面板（GET /api/desires + SSE desire_*）
        ActivityPanel.tsx    # 活动时间线（GET /api/activity + SSE activity_*）
        OutputsPanel.tsx     # 产出面板（GET /api/activity/results 跨天历史产出）
        NarrativePanel.tsx   # 自我叙事（GET /api/narrative）
        MaterialsPanel.tsx   # 资料上传（POST /api/upload + GET /api/materials）
        MemoryPanel.tsx      # 记忆浏览器（GET /api/memories + SSE memory_*）
        ReadingNotesPanel.tsx # 读书笔记（清单/详情/批注增删，GET /api/reading-notes + /api/annotations）
        EvalPanel.tsx        # eval + token 看板（GET /api/eval / GET /api/tokens）
      layout/
        Panel.tsx            # 通用面板容器
        InnerWorld.tsx       # 内心世界页内面板（横向子标签单分类，收 categoryIndex 渲染该类子标签条、点标签切活动面板，替换书卷区显示）
        SettingsView.tsx     # 游戏设置页内面板（字体大小 + 背景外观，替换书卷区显示）
      shell/
        LeftPanel.tsx        # 左面板（心情/精力展示 + 欲望一句话 + 活动一条）
        RightDock.tsx        # 右侧底部工具条（常驻：聊天 / 内在 / 空间 / 记录 / 出门 / 游戏设置，入口替换书卷区切视图）
        ScrollArea.tsx       # 书卷区域（对话主舞台 + 遭遇卡片）
    assets/
      sprites/               # 8 情绪 sprite（EmotionCategory 1:1）
  tests/
    api.test.ts              # API 端点测试（CLAUDE.md 要求）
    sse.test.ts
    stores.test.ts
    labels.test.ts           # 枚举中文化映射 + label() 回退
    activityResult.test.ts   # activityResult 纯函数（activitySubject/formatResult/activityAnnouncement）
```

### 命名约定（对齐 CLAUDE.md 前端规范）

- 组件 `PascalCase`、文件 `camelCase.tsx`；store/hook/api 文件 `camelCase.ts`。
- **TS 类型字段名 = 后端 JSON 键名（snake_case 原样，零映射）**：后端 dataclass 直接 `json.dumps`，字段是 `snake_case`（`valence` / `energy_state` / `current_activity` / `correlation_id`）。前端类型镜像时**不改名**，理由：零映射层 = 零映射 bug、字段名可沿 `correlation_id` 一路溯源到后端定义（原则 3/5）。前端内部 UI 变量名才用 camelCase，落类型时转 snake_case 键。

## 5. 面板去向（三区布局，06-game-shell）

design §11 列 7 个面板，全部落地。书卷风改造后，三区布局（06-game-shell）：左面板（心情/精力展示 + 欲望一句话 + 活动一条）｜书卷区域（对话主舞台）｜Galgame 对话框（输入 + 发送），右侧底部工具条（聊天 + 内在 + 空间 + 记录 + 出门 + 游戏设置，位于输入框上方）。点左面板摘要、右底工具条入口后，书卷区内容被**替换**为对应视图（替换式切视图，底部工具条 + 输入框常驻，切回聊天走工具条「聊天」入口，当前入口高亮）：每个词条**打开只切一次页面**——内在/空间/记录（顶部横向子标签条，点标签切活动面板）、出门（探索地图）、游戏设置（字体大小 + 背景外观）；左面板摘要与工具条同源分类（她现在的念头→内在 0、正在做什么→记录 2）。动作类词条（出门，将来的一起读书）与观测面板同一条工具条，未来加词条只往 `RightDock` 的 `ENTRIES` 追加一项。eval + token 走独立调试页（`Ctrl+Shift+D` 切换）。枚举值一律经 `lib/labels.ts` 转中文上屏（如 `exploration → 发现`），未知键回退原值。

| 面板 | 状态 | 数据源 | 组件落点 |
|---|---|---|---|
| 聊天区 | ✅ 实现（03-chat-panel） | `POST /api/chat` + SSE `speak`/`think`/`ask` | 书卷区「对话」模式（`components/shell/ScrollArea.tsx`） |
| 遭遇 | ✅ 实现（06-game-shell） | SSE `encounter_start`/`choice`/`end` + `POST /api/encounter/choose` | 书卷区「对话」模式（`components/encounter/EncounterCard.tsx`） |
| 背景外观 | ✅ 实现 | 无（纯前端 `settingsStore`） | `components/layout/SettingsView.tsx`（复用 `components/panels/BackgroundPanel.tsx`） |
| 内在状态面板 | ✅ 实现（04-inner-state-panel） | `GET /api/state` + SSE `emotion_update` | 左面板摘要 + `components/layout/InnerWorld.tsx`（内在分类） |
| 欲望面板 | ✅ 实现 | `GET /api/desires` + SSE `desire_*` | 左面板「她现在的念头」+ `InnerWorld`（内在分类） |
| 活动时间线 | ✅ 实现 | `GET /api/activity` + SSE `activity_*` | 左面板「正在做什么」+ `InnerWorld`（记录分类） |
| 产出面板 | ✅ 实现 | `GET /api/activity/results`（跨天历史产出） | `InnerWorld`（空间分类） |
| 自我叙事 | ✅ 实现 | `GET /api/narrative` | `InnerWorld`（内在分类） |
| 资料上传 | ✅ 实现 | `POST /api/upload` + `GET /api/materials` | `InnerWorld`（空间分类） |
| 读书笔记 | ✅ 实现 | `GET /api/reading-notes` + `GET/POST/DELETE /api/annotations` | `InnerWorld`（空间分类） |
| 记忆浏览器 | ✅ 实现 | `GET /api/memories` + SSE `memory_*` | `InnerWorld`（记录分类） |
| 探索地图 | ✅ 实现：逐层地牢（HUD/节点/托管/地图/道具占位） | `POST /api/explore` + `activityStore.results`（free_exploration 足迹） | `components/exploration/ExplorationMap.tsx`（工具条「出门」） |
| eval + token 看板 | ✅ 实现 | `GET /api/eval` / `GET /api/tokens` | 独立调试页（`components/panels/EvalPanel.tsx`，`Ctrl+Shift+D`） |

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
| `02-stores.md` | `chatStore` / `innerLifeStore` / 快照 store 的 state 形状 + actions |
| `03-chat-panel.md` | 聊天面板组件树、发消息流程、speak/think/ask 渲染 |
| `04-inner-state-panel.md` | 内在状态面板组件树、valence-arousal 图、精力条、情绪 sprite、Big Five/三观 |
| `05-client.md` | `client.ts` 薄 fetch 封装（postChat/getState/postObserve）+ 错误契约 |
