# 前端（Nyx Agent）

> React 前端，跑在 Tauri 薄壳里，通过 localhost HTTP/SSE 连接 Python 核心服务。
> 本文档集是前端实现索引：技术栈、目录结构、store 划分、SSE 数据流、面板边界和当前实现状态。后端契约以 `docs/specs/` 为准，具体实现以 `frontend/` 源文件为准。
> 范围：书页式完整窗口、聊天与阅读抽屉、内在/欲望/活动/任务/创作/记忆/知识库/评估/历史页面，以及保留原交互的桌宠小窗。

## 1. 技术栈

| 层 | 选型 | 依据 |
|---|---|---|
| 语言 | TypeScript（`strict: true`） | CLAUDE.md 前端规范 |
| UI | React 18（函数组件 + hooks） | CLAUDE.md |
| 状态 | Zustand（每系统一个 store） | CLAUDE.md |
| 构建 | Vite | React 常规工具链 |
| 测试 | Vitest + React Testing Library | Vite 生态，同构 TS |
| 桌面壳 | Tauri v2（透明桌宠窗口 + 完整 WebView） | `docs/specs/13-desktop-pet.md` |
| 通信 | SSE over localhost（实时）+ REST（快照） | `docs/specs/04-module-bus-system.md` / `docs/tech-reference.md` |

> 托盘菜单和系统通知仍未定义；Tauri 启动为透明、无边框、普通非置顶的小窗口，头像拖动会移动整个窗口，双击头像展开完整端，完整端使用左侧导航底部印章按钮收回桌宠。

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
│    12 个领域 spec 定义的 Facade / EventBus / LangGraph         │
└─────────────────────────────────────────────────────────────┘
```

- Python 核心作为**独立本地服务**运行（`uvicorn`），Tauri 壳 + React 前端通过 localhost HTTP/SSE 连接。开发态由 `dev.py --desktop` 配对启动后端与 Tauri；发布包内置 `nyx-backend` sidecar，由 Rust 壳启动、等待 8000 端口就绪并在退出时关闭，后端日志位于 Tauri app data 的 `backend.log`。
- 前端 Tauri 采集系统输入空闲毫秒数 + 窗口标题 → `classifyPresence` 判定 → `POST /api/observe`（04-module-bus-system 下游约定）。这是核心先行里唯一由前端发起的**被动上报**。

### 活跃度上报（`hooks/usePresence.ts`，核心先行唯一被动上报）

- **落点**：`hooks/usePresence.ts`，App 层挂载一次（与 `useSSE` 并列，见 01-sse §6），无 store（纯「采集 → 判定 → 上报」，不上屏，结果存后端 `last_presence`）。
- **判定镜像后端**（09-activity `observe.py`，规则逐字一致，不另造）：
  ```typescript
  type Presence = "online" | "away" | "busy";
  function classifyPresence(idleMs: number): Presence {
    if (idleMs < 30_000) return "online";
    if (idleMs < 300_000) return "busy";
    return "away";
  }
  ```
- **原生采样**：Windows Tauri `sample_presence()` 返回系统最后输入距今 `idle_ms` 与真实前台窗口标题；Rust 不应用 30 秒/5 分钟阈值。浏览器降级监听 `keydown`/`mousemove` 推导 idle，标题固定为空。
- **窗口标题**：只丰富观察内容，不参与 online/busy/away 判定；因此即使前台标题非空，空闲满 5 分钟仍为 away。
- **节奏**：采样独立于当前角色 SSE，每 30 秒采样，focus/恢复可见和 SSE open 时立即采样。每条携带稳定 sample_id、采样时角色 ID/revision 与 sampled_at。单请求在途，10 秒超时取消；失败保留有界 FIFO 队首及相同 sample_id，下次采样先重试，不以新 online 覆盖失败 away。队列最多 64，满时暂停采样；恢复后继续。切换不产生假 away，当前角色 FAILED/断线仍同步后台角色。原生非法值走 WebView fallback，标题限制 512 字符。

## 3. 数据流（双通道）

| 通道 | 用途 | 端点 |
|---|---|---|
| REST | 初始快照 + 发消息 + 历史查询 + 导出 | 15 个端点（tech-ref §4） |
| SSE | 当前角色实时事件 | `GET /api/characters/{id}/events` |

- **SSE 是主通道**：后端广播全部事件，前端按 `event` 类型增量更新面板；REST 只做进页面时的初始快照 + 用户主动动作（发消息/导出）。事件语义见 `docs/specs/04-module-bus-system.md`。
- SSE `data` 统一形状（tech-ref §4）：

```json
{"character_id": "nyx", "event_id": "…", "correlation_id": "…", "timestamp": 1789603200.0, **event.content}
```

- 核心先行用到的端点：`GET /api/state`（`CurrentState` 快照）、`POST /api/chat`（发消息，返回 `{event_id}`，可附 `reply_to` 精确绑定提问，回复走 SSE）、`GET /api/questions/pending` 与提问 pause/resume 端点（恢复聊天提问计时）、`POST /api/observe`（活跃度上报 `{presence, window_title, idle_seconds, sampled_at}`，返回 `{event_id}`，见 §2）、`GET /api/events`（SSE）。

## 4. 目录结构

角色入口：`api/characterScope.ts` 固定请求归属，`stores/characterStore.ts` 串行切换和重置展示，`components/layout/CharacterSelect.tsx` 与 `CharacterManager.tsx` 提供标题/桌宠选择和设置管理。业务端点自动加角色命名空间；以下旧路径均为业务后缀，`/api/observe` 保持宿主入口。详见 [14-character-cards](../specs/14-character-cards.md)。

```
frontend/
  package.json
  vite.config.ts
  tsconfig.json              # strict: true
  index.html
  src/
    main.tsx                 # React 入口，挂载 App
    App.tsx                  # 昼夜状态、顶格左侧图标导航、聊天与阅读抽屉/页面、桌宠模式
    types/
      api.ts                 # 后端契约 TS 镜像（Event/CurrentState/EmotionCategory/…）
    lib/
      labels.ts              # 枚举值→中文 UI 标签（label() 未知键回退原值）
      activityResult.ts      # 活动产出纯函数（activitySubject / formatResult / formatOutputBody / formatOutputPath / formatTools / activityAnnouncement）
      time.ts                # 本地昼夜、时钟/消息时间标签与时间分隔纯函数
      desktopWindow.ts       # Tauri 桌宠/完整窗口尺寸、层级与原生拖动适配
    api/
      client.ts              # REST fetch 封装（含记忆搜索/事实快照，见 05-client）
      dispatch.ts            # SseEvent → store 路由（01-sse §4.1）
    hooks/
      useSSE.ts              # SSE 订阅 hook（CLAUDE.md 点名）
      usePresence.ts         # 活跃度采集 + classifyPresence 判定 + POST /api/observe
      useTypewriter.ts       # 打字机：nyx 文本逐字显示（纯渲染层，03-chat-panel）
    stores/
      chatStore.ts           # 聊天：消息列表 + 历史加载 + addReadingTurn
      innerLifeStore.ts      # 内在状态：CurrentState 快照
      desireStore.ts         # 欲望：DesireState 快照（快照 store）
      activityStore.ts       # 活动：ActivitySnapshot + 创作 results + 委派任务快照/创建
      memoryStore.ts         # 记忆分类检索 + 三种独立事实图：Memory/MemoryFact 快照 + SSE memory_*
      readerStore.ts         # 阅读：书架/进度/段落/追赶/笔记 + paginate 真分页纯函数
      settingsStore.ts       # 背景外观：tint/image/fontScale + 圆圈底色/尺寸/位置 circleColor/circleSize/avatarPos（后三者持久化 localStorage）
      announceStore.ts       # 立绘旁临时气泡：items/announce/dismiss（纯前端呈现，无后端）
    components/
      desktop/
        PetShell.tsx          # 头像东西南北四点入口、聊天/选书/陪读小面板与状态气泡
      AnnounceLayer.tsx      # 桌宠头像上方/完整窗口右下角的临时气泡
      chat/
        MessageList.tsx
        MessageBubble.tsx
        ChatInput.tsx
      inner/
        InnerStatePanel.tsx
        ValenceArousalPlot.tsx
        EmotionSprite.tsx
        Avatar.tsx           # 可拖拽头像圆圈：拖拽/菜单入口/红点通知（包裹 EmotionSprite + AnnounceLayer；见 08 §4/§8）
        EnergyBar.tsx
        BarChart.tsx
        BigFiveChart.tsx
        ValuesChart.tsx
      panels/
        BackgroundPanel.tsx  # 背景外观（预设色调/自定义取色/上传背景图/恢复默认）
        DesiresPanel.tsx     # 欲望面板（GET /api/desires + SSE desire_*）
        ActivityPanel.tsx    # 活动页：当天 U 形时间线；任务页：委派表单/五条分页/侧边详情
        CreationPanel.tsx    # 创作书架、正文和活动来源，12 条一批加载更多
        MemoryPanel.tsx      # 普通记忆/知识库分区检索、五条分页及各作用域事实图
        FactGraph.tsx         # 手写 SVG 实体关系图（不引入图表依赖）
      reading/
        BookshelfView.tsx    # 书架（GET /api/books + 导入 EPUB）
        ReaderView.tsx       # 阅读页：真分页（paginate + 测量/重测，08 §5）
        NotePanel.tsx        # 笔记面板（阅读事件文档）
      layout/
        Panel.tsx            # 通用面板容器
        Modal.tsx            # 通用弹层容器
        SettingsView.tsx     # 完整端设置页/桌宠设置弹层：左侧外观 + 右侧角色管理和真实 canon
      shell/
        RightDock.tsx        # 左侧图标导航，含任务/记忆/历史及设置入口、桌宠返回印章
        StatusBar.tsx        # 既有状态条组件，完整窗口不再挂载
    assets/
      expressions/          # 8 情绪表情图（EmotionCategory 1:1，方形 1080×1080）
  tests/
    api.test.ts              # API 端点测试（CLAUDE.md 要求）
    memoryPanel.test.tsx     # 记忆搜索、事实图和事实降级测试
    sse.test.ts
    stores.test.ts
    labels.test.ts           # 枚举中文化映射 + label() 回退
    activityResult.test.ts   # activityResult 纯函数（activitySubject/formatResult/formatOutputBody/formatOutputPath/formatTools/activityAnnouncement）
```

### 命名约定（对齐 CLAUDE.md 前端规范）

- 组件 `PascalCase`、文件 `camelCase.tsx`；store/hook/api 文件 `camelCase.ts`。
- **TS 类型字段名 = 后端 JSON 键名（snake_case 原样，零映射）**：后端 dataclass 直接 `json.dumps`，字段是 `snake_case`（`valence` / `energy_state` / `current_activity` / `correlation_id`）。前端类型镜像时**不改名**，理由：零映射层 = 零映射 bug、字段名可沿 `correlation_id` 一路溯源到后端定义（原则 3/5）。前端内部 UI 变量名才用 camelCase，落类型时转 snake_case 键。

## 5. 面板去向（精简装配）

完整端采用「双开本 / 编年史」外观：移除原型预览顶栏，左侧导航顶格，底部印章按钮返回桌宠；窗口风格在设置页切换；左侧为图标导航；默认聊天页可展开右侧阅读抽屉，阅读/聊天为 7:3。其他模块独立呈现，切换不清空领域 store；完整端不挂载浮动 Avatar。内在保留紧凑数值卡，欲望/记忆/历史/评估/任务使用检索与分页，创作使用书架。App 每分钟更新时钟，在 06:00/22:00 切换昼夜视觉；不改变后端内在状态。页面及翻页动效遵循减少动态效果偏好。枚举值经 `lib/labels.ts` 转中文，未知键回退原值。

| 面板 | 状态 | 数据源 | 组件落点 |
|---|---|---|---|
| 聊天区 | ✅ 实现（03-chat-panel） | `POST /api/chat` + SSE `speak`/`think`/`ask` | 默认聊天页：`MessageList.tsx` + `ChatInput.tsx` |
| 内在状态面板 | ✅ 实现（04-inner-state-panel） | `GET /api/state` + SSE `emotion_update` | `components/inner/InnerStatePanel.tsx`（`view==="inner"`） |
| 欲望面板 | ✅ 实现 | `GET /api/desires` + SSE `desire_*` | `components/panels/DesiresPanel.tsx`（`view==="desire"`） |
| 活动与委派任务 | ✅ 实现 | `GET /api/activity`、`GET/POST /api/tasks*`（含浏览器问卷）+ SSE `activity_*`/`task_updated` | `ActivityPanel.tsx`，分别为活动页和任务页 |
| 创作 | ✅ 实现 | `GET /api/activity/results?activity_type=creation&limit=&offset=` | `components/panels/CreationPanel.tsx`（`view==="creation"`） |
| 记忆面板 | ✅ 实现 | `GET /api/memories`、`GET /api/memories/search`、`GET /api/memories/facts` + SSE `memory_*` | `components/panels/MemoryPanel.tsx` + `FactGraph.tsx`（`view==="memory"`） |
| 知识库 | ✅ 基于现有数据 | 知识类别记忆与知识作用域事实 | `MemoryPanel.tsx`（记忆页右上角切换知识库）+ `FactGraph.tsx` |
| 读书 | ✅ 实现（06/07） | `GET /api/books` + 进度/段落/笔记端点 | 聊天右侧抽屉：`BookshelfView.tsx` + `ReaderView.tsx` |
| 背景外观 | ✅ 实现 | 无（纯前端 `settingsStore`） | `components/layout/SettingsView.tsx`（复用 `components/panels/BackgroundPanel.tsx`） |
| 桌宠模式 | ✅ 实现 | Tauri 窗口配置 + 纯前端 UI 状态 | `components/desktop/PetShell.tsx`、`lib/desktopWindow.ts` |

## 6. 测试约定

- CLAUDE.md：「所有 API 端点必须有测试」→ `tests/api.test.ts` 覆盖 `client.ts` 的每个端点（mock fetch，断言请求 URL/方法/响应解析）。
- SSE：`tests/sse.test.ts` 与 `tests/connection.test.tsx` 用 mock `ReadableStream` 验证 `useSSE` 的分块解析、Bearer、续传和分发给 store。
- stores：`tests/stores.test.ts` 验证每个 store 的 action 纯逻辑（不依赖真实网络）。
- 测试不依赖真实后端/真实桌面；验证管道正确性（事件走对 store、类型解析正确），不验证视觉样式。
- 同一行为优先保留能观察最终结果的用例，删除重复的内部转换、静态声明和仅检查 CSS 类名/容器存在的测试；事件接线、可变默认值隔离、持久化、失败恢复、幂等和乱序回归必须保留。

## 7. 文档索引

| 文件 | 覆盖 |
|---|---|
| `README.md` | 本文（总览） |
| `01-sse.md` | `useSSE` fetch reader、data 形状、EventType→store 分发表、重连容错 |
| `02-stores.md` | `chatStore` / `innerLifeStore` / 快照 store 的 state 形状 + actions |
| `03-chat-panel.md` | 聊天面板组件树、发消息流程、speak/think/ask 渲染、共享时间纯函数与时间分隔 |
| `04-inner-state-panel.md` | 内在状态面板组件树、valence-arousal 图、精力条、情绪 sprite、Big Five/三观 |
| `05-client.md` | `client.ts` 薄 fetch 封装（postChat/getState/postObserve）+ 错误契约 |
| `06-reading-panel.md` | 阅读面板：书架 + 阅读页 + `readerStore` + Nyx 追赶（`setTimeout` 秒级逐段） |
| `07-reading-events.md` | 阅读事件：读书提问/联想并进对话 + 碎碎念悬浮气泡 + 笔记面板 |
| `08-reading-chat-layout.md` | App 布局、共享分钟时钟、昼夜视觉、头像圆圈、真分页与桌宠模式 |
