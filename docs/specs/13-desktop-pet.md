# 桌宠窗口与轻量入口

> 本 spec 定义桌面端桌宠态与完整桌面端之间的 UI/窗口契约；不新增后端事件、数据库表或业务 Facade。

## 元信息

- **前置依赖**：前端 `08-reading-chat-layout`；Tauri v2 窗口配置；现有聊天、阅读 Zustand store
- **实现文件**：[`frontend/src/App.tsx`](../../frontend/src/App.tsx)、[`frontend/src/components/desktop/PetShell.tsx`](../../frontend/src/components/desktop/PetShell.tsx)、[`frontend/src/components/inner/Avatar.tsx`](../../frontend/src/components/inner/Avatar.tsx)、[`frontend/src/lib/desktopWindow.ts`](../../frontend/src/lib/desktopWindow.ts)、[`frontend/src-tauri/tauri.conf.json`](../../frontend/src-tauri/tauri.conf.json)、[`frontend/src-tauri/capabilities/default.json`](../../frontend/src-tauri/capabilities/default.json)

## 用户故事

> 作为 Nyx 用户，我想让 Nyx 以一个可拖动的圆球常驻桌面，并从圆球快速进入聊天或陪伴读书；需要完整操作时，双击头像进入与网页端相同的桌面界面。

## 验收标准

- [ ] Tauri 启动窗口透明、无边框、使用普通非置顶窗口层级（其他应用激活时自然位于其后），桌宠态尺寸为 `560×520`；完整态尺寸为 `1200×820` 并可调整大小。
- [ ] 桌宠态只显示可拖动 Avatar 和位于头像下方的紧凑轻量入口，不显示完整网页布局或独立状态栏。
- [ ] Tauri 桌宠拖拽移动整个窗口，可在整个桌面范围内移动；浏览器开发环境保留窗口内头像拖拽回退。
- [ ] 单击 Avatar 打开头像正下方的紧凑圆弧菜单；菜单包含「聊天」「读书」「内心」「设置」四个入口，不遮挡 Avatar。
- [ ] 双击 Avatar 在任意桌宠子界面进入完整桌面端；完整桌面端再次双击 Avatar 收回桌宠态。
- [ ] 当前状态在 Avatar 头顶以常驻气泡显示；「内心」入口提供情绪、精力、当前活动和简短感受摘要。
- [ ] 聊天入口显示无外框 Galgame 单行对白与悬空输入框；发送复用 `chatStore.sendMessage`。
- [ ] 读书入口先显示书目选择；选择书籍后显示陪读内容、Nyx/用户段落位置、读书上方的主动对话气泡和悬空输入框；选书页与陪读页顶部均有无文字 `→` 返回按钮。
- [ ] 陪读页提供「上一页」「下一页」按钮；按钮复用 `readerStore.syncPosition` 逐段移动用户进度，并在第 1 段/末段禁用对应方向。
- [ ] 「设置」入口打开可修改的设置面板，修改复用现有 `settingsStore`（字体、圆圈颜色/大小、背景色调和背景图等）。
- [ ] 当前状态以 Avatar 头顶常驻气泡显示；精力显示为整数百分比，详细状态继续由完整端提供。
- [ ] 桌宠入口不复制聊天或阅读业务逻辑；桌宠/完整态切换不清空现有 store 状态。
- [ ] 浏览器开发与测试环境不依赖 Tauri API：显示完整端，窗口适配函数安全降级为 no-op；Tauri 环境使用原生窗口拖动。

## 技术方案

- **状态**：`App` 持有不可持久化的 `DesktopMode = "pet" | "full"`；`PetShell` 持有当前轻量面板（圆弧入口、聊天、选书、陪读、内心）；设置仍由 `App` 打开现有可修改 `SettingsView`。
- **窗口**：`desktopWindow.ts` 按模式调整逻辑尺寸、可调整大小与窗口层级；桌宠与完整态都关闭置顶和 always-on-bottom，保持普通窗口层级；Tauri 配置提供透明、无边框初始窗口。
- **头像**：`Avatar` 通过显式 `useNativeWindowDrag` 区分模式：桌宠态在 Tauri 中先用位移阈值区分点击与拖动，越过阈值后交给原生窗口拖动；完整端即使运行在 Tauri 中也只在窗口内夹取/移动头像；浏览器继续保留现有头像位置持久化与拖动；新增 `onActivate`、`onDoubleClick`、`children` 和 `showAnnouncements`，由桌宠层组合菜单与面板。
- **入口交互**：头像单击切换正下方四项圆弧入口展开/收起；头像双击进入完整端；「设置」回调打开现有设置弹层，「内心」进入轻量状态摘要。
- **主动气泡**：当前状态气泡常驻 Avatar 头顶；陪读小面板读取 `announceStore.items` 的最新项；非陪读面板仍由 `AnnounceLayer` 显示在头像上方。
- **不变更**：后端 API、SSE 事件、数据库、聊天历史、阅读进度和完整桌面端导航均不改变。

## 测试要点

- [ ] `avatar.test.tsx`：单击/双击回调、红点清除、昼夜表情与拖动坐标纯函数。
- [ ] `petShell.test.tsx`：四项圆弧菜单、内心摘要、可修改设置入口、聊天/读书面板、状态气泡和双击展开回调；读书选择、返回箭头和陪读翻页按钮保持同一面板层级。
- [ ] `avatar.test.tsx`：完整端与桌宠态的 Tauri 拖动模式隔离，完整端不调用原生窗口拖动。
- [ ] `npm run build`：TypeScript 与 Vite 构建通过。
- [ ] Tauri `cargo check`：窗口配置对应 Rust 壳可编译。

## 完成定义

- [ ] `npm test -- --run` 全绿
- [ ] `npm run build` 全绿
- [ ] `cargo check` 全绿
- [ ] `docs/frontend/README.md`、`docs/frontend/08-reading-chat-layout.md`、`docs/test-inventory.md` 已同步
