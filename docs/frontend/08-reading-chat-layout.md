# 08 阅读 × 聊天统一布局（左栏常驻对话 + 真分页 + 可拖拽头像圆圈 + 碎碎念浮泡）

> 前端「陪伴感」重构：聊天从中间舞台挪到**左栏常驻**（读书/看面板时都能聊）；读书改**真分页**（取消滚动）；立绘做**可拖拽头像圆圈**（白底可换底色，碎碎念头顶冒）；碎碎念改**悬浮气泡**；读书提问/联想**并进对话**。
> 对齐后端：`24-reading-chat-turn`（读书提问/联想进 `_history`，回复可引用）。

> **行号是定位锚，不是指令**：本文行号只用于快速定位；落点以**符号名 + 变量名**为准。

## 1. 布局重构（App 装配 + 复用既有组件，不造新组件）

`.game-shell` 由「左状态条 + 中间书卷区 + 底部输入框/工具条」改为「**左栏对话 + 中间内容区**」。**复用** `StatusBar` + `MessageList` + `ChatInput` 三个既有组件竖排成左栏，不新增 `LeftChatDock`（反冗余）。

```
┌───────────────┬──────────────────────────────────────────┐
│ 左栏对话       │   中间内容区（game-main，position:relative）│
│  StatusBar(瘦)│   · view==="reading" → ReaderView/书架      │
│  MessageList  │   · 其余 → 内在/欲望/活动/记忆面板            │
│  (滚动)        │   · 右下角：可拖拽头像圆圈（§4，position:fixed 浮窗） │
│  ChatInput(扁)│   底部：导航（读书|内在|欲望|活动|记忆）       │
└───────────────┴──────────────────────────────────────────┘
```

- **栅格改两行**：`.game-shell` `grid-template-columns: 340px minmax(0,1fr)`（左栏宽 **340px** 不变）；`grid-template-rows: minmax(0,1fr) auto`（**删第 3 行**，ChatInput 不再占右栏底部行）。
- **左栏**（新 `div.left-dock`，`grid-column:1; grid-row:1/span 2`，`display:flex; flex-direction:column; gap:12px`）：`StatusBar`（瘦身，见下）→ `MessageList`（`flex:1`，滚动，占满中段）→ `ChatInput`（底部，`flex-shrink:0`）。聊天**全局唯一**，常驻不随切视图消失。
- **`StatusBar` 瘦身**：删掉 `<Avatar />`（立绘迁到 §4 圆圈）与「✦ Nyx ✦」名字（聊天区被挤，名字占顶栏标题位即可），只留 `status-bar__info`（心情/精力/现在状态）；组件内不再 import `Avatar`/`EmotionSprite`（清 orphan）。
- **中间**（`.game-main`，`grid-column:2; grid-row:1; position:relative`）：`view` 类型删 `null`（聊天不再是可切换视图）；默认 `view = "reading"`（开应用即书架 + 左栏常驻对话）。`ScrollArea`（原聊天舞台）删除。
- **导航**：`RightDock` 的 `ENTRIES` 删「聊天」一项（`{label:"聊天", view:null}`），剩 `读书|内在|欲望|活动|记忆` 五个，只切中间。`RightDock` `grid-column:2; grid-row:2`。
- **反冗余**：删除 `ScrollArea.tsx`（聊天舞台）与 `MutterCard.tsx`（碎碎念卡片）两个被替代组件 + `mutterStore.ts`（§3）。

## 2. 读书反应并进对话（类型扩展 + dispatch 重路由 + 渲染契约）

后端 24 不改事件（仍发 `READING_QUESTION`/`READING_ASSOCIATION`/`READING_MUTTER`），前端**重路由**进 `chatStore`。

### 2.1 `ChatMessage` 类型扩展（`chatStore.ts`）

```ts
export type ChatMessage = {
  id: string;
  role: "user" | "nyx";
  kind:
    | "message" | "speak" | "ask" | "think" | "initiate_chat"
    | "reading_question" | "reading_association";
  content: string;
  correlation_id: string;
  preloaded?: boolean;
  // 读书 turn 专属（kind==="reading_question" 才有 subtype/selectedText；"reading_association" 才有 memoryId）
  subtype?: QuestionSubtype;
  selectedText?: string | null;
  memoryId?: string;
};
```

- `QuestionSubtype` 从 `types/api.ts` import（已存在，零映射）。

### 2.2 `chatStore.addReadingTurn`（新 action）

```ts
addReadingTurn: (e: ReadingQuestionEvent | ReadingAssociationEvent) => void
```

- **question** → `{ kind:"reading_question", content:e.content, subtype:e.subtype, selectedText:e.selected_text, correlation_id:e.book_id }`。
- **association** → `{ kind:"reading_association", content:e.snippet, memoryId:e.memory_id, correlation_id:e.book_id }`。
- **`correlation_id = e.book_id`**：后端 `internal_event(..., book_id)` 用 `book_id` 当 correlation_id（`ReadingQuestionEvent/ReadingAssociationEvent` 都有 `book_id` 字段），前端照填，`ChatMessage.correlation_id` 必填有出处。
- **不过滤当前书**：气泡流迁走后，读书 turn 是**永久聊天消息**（同 `initiate_chat`），不再做「只收当前书」过滤——关书后她读那本书的提问/联想仍留在聊天转录里，正是「记得自己刚问过什么」。删除 readerStore 里 `addReadingBubble` 的 `e.book_id !== get().bookId` 守卫（§2.4 一并删 `addReadingBubble`）。
- 复用 `append` 的「文本字段非 string 丢弃」收窄校验（question 校验 `content`、association 校验 `snippet`，非 string 丢帧不崩）。

### 2.3 `dispatch.ts` 重路由

```ts
case "reading_question":
case "reading_association":
  return useChatStore.getState().addReadingTurn(e);
case "reading_mutter":
  // 归入 §3 碎碎念气泡，与全局 mutter 统一走 announce
  return useAnnounceStore.getState().announce("mutter", e.content);
```

- 三个 reading 事件**不再**走 `useReaderStore.getState().addReadingBubble(e)`。

### 2.4 `readerStore` 删气泡流

- 删除 `impulseBubbles` state、`addReadingBubble` action、`ReadingBubble`/`ReadingBubbleKind` 类型、`_BUBBLE_CAP` 常量、`ReadingMutterEvent/ReadingQuestionEvent/ReadingAssociationEvent` import（清 orphan）。读书 store 只留书架/进度/段落/追赶/笔记。

### 2.5 `MessageBubble` 渲染契约

- **即时全量，不逐字**：读书 turn **不进** `MessageBubble.isNyxText` 白名单（`speak/ask/think/initiate_chat`），也不进 `MessageList.NYX_TEXT_KINDS`（`isReady` 的白名单）——两个白名单**都不加** reading 两 kind。结果：`typewrite=false`、`isReady` 恒 `true`，读书 turn 即时渲染、不进打字机串行门。理由：它是读书时的冲动反应，非对用户的回复，逐字无意义且多条联想（每条记忆一条 turn，≤3）会排队。
- **徽标**：`kind==="reading_question"` → `<span className="message-bubble__badge">提问</span>`；`kind==="reading_association"` → `联想`（沿用 ReaderSidebar 的 `BUBBLE_LABEL` 文案）。
- **划线引用**：`message.selectedText` 非空（仅 `quote_question`）→ 内容下方渲染 `<p className="message-bubble__quote">原文：「{selectedText}」</p>`（复用 `.note-item__quote` 的视觉语言：左金线 + 斜体小字）。
- **记忆标**：`message.memoryId` 存在 → 渲染 `<span className="message-bubble__memory">记忆</span>`（轻量非交互标记，暗示「这段联想来自一条记忆」；`memoryId` 值留在消息字段供未来点开溯源，MVP 不渲染 id 原文）。
- `role="nyx"` 照常 → `message-bubble--nyx` 气泡样式；`kind` 落到 `message-bubble--reading_question/--reading_association` class（无额外样式也可，不强制）。

## 3. 碎碎念悬浮气泡（mutter 统一 + reflection 收尾）

- `dispatch.ts` 两处归入 `announceStore`：`mutter` case 改 `useAnnounceStore.getState().announce("mutter", e.content)`（删 `useMutterStore` import 与 `addMutter` 调用）；`reading_mutter`（§2.3）同样 `announce("mutter", e.content)`。
- **`reflection_done` 不动**：已 `announce("mutter", …)`（`dispatch.ts` 现 73-86），与 mutter/reading_mutter **共享 `announce` 的 `"mutter"` kind**——`"mutter"` kind 语义统一为「Nyx 轻声自语/反思的瞬时气泡」，三者同一渲染路径，无冲突。
- **删** `mutterStore.ts` + `MutterCard.tsx`（碎碎念不再有「最近几条卡片」，改为瞬时气泡几秒淡出）。
- `AnnounceLayer` **重新定位**：从「头像旁」（`.announce-layer` `left:16px; bottom:64px`）移到「头像圆圈头顶上方」并**嵌套进 `Avatar` 圆圈内**（随圆圈拖拽走）——`.announce-layer` 改 `position:absolute; left:50%; bottom:calc(100% + 8px); transform:translateX(-50%); align-items:center`（气泡居中、贴着圆圈头顶）。`ANNOUNCE_DURATION.mutter = 4000` 不变；`announceStore` 本体不动（`announce`/`dismiss`/`ANNOUNCE_DURATION` 已够用）。

## 4. 可拖拽头像圆圈（白底可换底色 + 拖拽 + 头顶气泡）

`Avatar` 改成**可拖拽圆形头像**：`position:fixed` 浮在窗口右下角，`EmotionSprite` 头部裁进圆圈，可拖到窗口任意处、位置/底色/尺寸存 localStorage；碎碎念气泡（`AnnounceLayer`）嵌套在圆圈内、头顶冒出随圆圈走。

- **结构**：`App.tsx` 直接挂 `<Avatar />`（不再包 `.avatar-overlay`）；`AnnounceLayer` 移到 `Avatar` 内部（气泡跟随圆圈），`App` 不再单独挂 `<AnnounceLayer />`。顶栏「设置」按钮删除，设置入口迁到 `RightDock`（底部导航新增「设置」项）。
- **`Avatar.tsx` 重写**：白底圆形（`backgroundColor = settingsStore.circleColor`，默认 `#ffffff`）；`position:fixed; right:24px; bottom:24px; border-radius:50%`，`width/height` 内联自 `settingsStore.circleSize` 三档（`CIRCLE_SIZES`：小 96 / 中 120 / 大 144，默认大），内层 `.avatar-circle__face` `overflow:hidden` + `EmotionSprite size="circle"`（`object-fit:cover` 方形表情图撑满不裁，图源 `assets/expressions/`）。位置记忆 `avatarPos` 非 null 时内联 `left/top` 覆盖默认右下角。表情图 `<img>` 加 `draggable={false}`、`.emotion-sprite--circle` 加 `pointer-events:none`，防浏览器原生图片拖拽抢占圆圈拖拽。
- **拖拽**：`onPointerDown/Move/Up/Cancel` + `setPointerCapture`；`getBoundingClientRect()` 记录起点，位移超 `DRAG_THRESHOLD=3` 判定为拖拽（否则算戳）；拖拽中本地 `dragPos` 渲染、松手才 `setAvatarPos` 提交（一次 localStorage 写）；`clampAvatarPos` 把坐标夹回视口内。挂载时若记忆坐标越界（窗口变小）自动夹回。
- **戳立绘交互保留**：`handlePoke` 连续戳害羞 `SHY_PHRASES`、≥5 次生气 `ANGRY_PHRASES`、1.5s 停手复位 `POKE_RESET_MS`、戳时 `announce("mutter", …)`；`moved` 守卫让「拖拽后的 click」不误触发戳。红点通知（`.avatar-notice`）改为纯红点（`aria-label="小狐狸我有话对你说"`），点击 `stopPropagation` 清除 `unreadProactive`。
- **持久化**：`settingsStore` 新增 `circleColor`/`circleSize`/`avatarPos` + 对应 setter，读写 localStorage（键 `nyx.circleColor`/`nyx.circleSize`/`nyx.avatarPos`），不可用时静默降级；`reset()` 一并清这三个键。`EmotionSprite` 的 `size` 变体删 `portrait`、增 `circle`（`portrait` 无调用方，清 orphan）。
- **设置项**：`SettingsView` 新增「圆圈背景」面板（预设白/浅粉/浅蓝/浅绿/浅橙/淡紫 + 自定义取色），复用 `.bg-panel` 色块；预设名与「背景色调」预设（樱粉/晨蓝/…）错开，避免测试按名选色歧义。另新增「圆圈大小」三档（小/中/大，复用 `.font-scale__opt` 按钮，aria-label 用「圆圈小/中/大」与字体三档错开）。

## 5. 真分页（取消滚动，纯函数分页 + 测量/重测契约）

`ReaderView` 由「滚动容器 + `onScroll`/`offsetTop`/`scrollBy`」改为**真分页**：容器 `overflow:hidden`，无滚动条、无滚轮，翻页整页切换。

### 5.1 纯函数 `paginate`（`readerStore.ts` 导出，同 `computeWindow` 纯函数族）

```ts
export function paginate(
  paragraphs: Paragraph[],
  measureHeight: (index: number) => number, // 第 index 段（全局 1-based）渲染高度 + 段间距
  viewportHeight: number,
): number[][]
```

- **贪心填满**：从窗口首段起累计 `measureHeight`，加下一段将溢出 `viewportHeight` 则封页、下一段开新页；返回页数组，每页 = 该页段落 `index`（全局 1-based，与 `Paragraph.index` 一致）升序。单段高于 `viewportHeight` → 独占一页；`paragraphs` 空或 `viewportHeight<=0` → `[]`。
- **作用域**：只对**当前窗口**（`readerStore.paragraphs`，≤50 段）分页，非整本书；窗口末尾翻页触发重拉（§5.5）。
- **`measureHeight(index)`**：`index` 是全局段号（`Paragraph.index`），实现为 `(paraRefs.current.get(index)?.offsetHeight ?? 0) + GAP_PX`。`GAP_PX` 是 `readerStore` 模块级常量 `= 12`（对齐 CSS `.reader-text` 的 `gap: 0.75rem`），段间距计入分页高度——否则多段页会轻微溢出裁掉末段。

### 5.2 测量触发（重测重分页）

- **量哪个元素**：每段 `<p>` 的 `offsetHeight`（段 `margin:0`，只有内容+padding，`offsetHeight` 即段高）。用现有 `paraRefs`（`ref` 回填 `Map<number, HTMLParagraphElement>`，键 = `p.index`）。
- **`viewportHeight`**：`.reader-text` 容器 `clientHeight`，由 `ResizeObserver` 维护成组件 state。
- **重测时机**：`useLayoutEffect` 依赖 `[paragraphs, fontScale, viewportHeight, windowFrom]`——任一变化重测全部段高 + 重分页。覆盖三处：`fontScale` 变（`--text-scale` 改字号 → 段高变）、窗口 resize（`viewportHeight` 变）、换书/窗口重拉（`paragraphs`/`windowFrom` 变）。

### 5.3 状态归属（持久态 vs 瞬态）

- **持久态（`readerStore`）**：`userPosition` = **当前页首段 index**，唯一落库（`putProgress` 已有）。不新增 store 字段。
- **瞬态（`ReaderView` 组件 state）**：`pageIndex`（当前页序号，0-based）、`heights`（段高 Map）、`pages`（`paginate` 结果）、`viewportHeight`。这些是 DOM 测量派生值，**不入 store**（store 状态须可序列化，DOM 高度不是）。
- **`pageIndex` 从 `userPosition` 反推**：`pageIndex = pages.findIndex(p => p.includes(userPosition))`，找不到 → `0`；再 `clamp [0, pages.length-1]`。`userPosition` 是唯一事实来源，重分页后靠它找回页序，不丢位置。

### 5.4 翻页映射

- `上一页/下一页` 按钮不再调 `scrollBy`，改 `setPageIndex(pageIndex ± 1)`（clamp `[0, pages.length-1]`）。
- 页序变化后，**页首段 = `pages[pageIndex][0]`** → 调 `syncPosition(页首段)`（复用既有 `syncPosition` 的「前翻逐段补发 `evaluateImpulse` + `putProgress` + 必要时 `fetchWindow` + `startCatchup`」整条管线）。`userPosition` 恒等于当前页首段。
- 视觉切换：内容 wrapper `transform: translateY(-页前累计高度)`（`-sum(measureHeight(前页各段))`，CSS `transition` 平滑），`overflow:hidden` 裁掉其余页；无滚动条、无滚轮。高亮 `--current`（`userPosition` 段）+ 🦊 `--nyx`（`nyxPosition` 段）保留（06 既有 class，只改「当前段」判定为 `pages[pageIndex][0]`）。

### 5.5 窗口刷新不漂

- `syncPosition` 内 `needsWindowRefresh` → `fetchWindow(centered=false)`（既有，**不改**），重拉后 `windowFrom = userPosition`、新窗口首段即当前页首段。
- `windowFrom` 变化触发重分页（§5.2），`pageIndex` 归 `0`（新窗口从当前段开始），`userPosition` 恒为页首段——**页状态不漂**。

### 5.6 删除项

- 删 `ReaderView` 的 `containerRef` 滚动逻辑：`handleScroll`（`onScroll`+`offsetTop`+rAF）、`page()` 的 `scrollBy`、`.reader-text` 的 `onScroll`。`useEffect` 里的 `scrollTop=0` 改为「重分页后 `pageIndex` 归位」逻辑。
- `ReaderSidebar` 拆解（§6 的 06 反向修订）：侧栏删除，内容四散——追赶进度 → 读书 header 一行「她读到第 K 段 / 你读到第 M 段」；冲动气泡 → §2/§3；「笔记」入口 → 读书 footer `重读` 旁（`NotePanel` 本体不动）。

## 6. 对既有前端 spec 的反向修订

- `01-sse.md`：分发表 `reading_question`/`reading_association` 改路由 `chatStore.addReadingTurn`；`reading_mutter` → `announceStore`；`mutter` → `announceStore`（原 mutterStore）；`reflection_done` 保持 `announce("mutter")`。
- `02-stores.md`：`chatStore` 增 `addReadingTurn` + `ChatMessage` 扩 `reading_*` kind 与 `subtype/selectedText/memoryId`；`readerStore` 删 `impulseBubbles`/`addReadingBubble`/`ReadingBubble`/`ReadingBubbleKind`；`mutterStore` 条目删除。
- `03-chat-panel.md`：聊天主区从「中间舞台」改「左栏常驻」；`ScrollArea` 删除；`MessageBubble.isNyxText` 与 `MessageList.NYX_TEXT_KINDS` 明确**不加** reading 两 kind（即时渲染）。
- `06-reading-panel.md`：§5「滚动容器 + onScroll」改「真分页」（`paginate` + 测量/重测/翻页契约）；`ReaderSidebar` 拆解（进度→header、气泡迁走、笔记→footer）；`readerStore` 删 `turnPage`（本 spec 已由 `syncPosition` 语义承载，§5.4 直接调 `syncPosition`）。
- `07-reading-events.md`：§2「冲动气泡（ReaderSidebar）」改「并进对话/悬浮气泡」。
- `types/api.ts`：**不改**（`ReadingQuestionEvent`/`ReadingAssociationEvent`/`ReadingMutterEvent`/`QuestionSubtype` 已存在；`ChatMessage` 在 `chatStore.ts` 非此文件）。

## 7. 测试要点

- `chatStore.addReadingTurn`（`tests/stores.test.ts`）：question → `kind==="reading_question"`、`subtype`/`selectedText` 落对、`correlation_id===book_id`；association → `kind==="reading_association"`、`memoryId`/`snippet`(→content) 落对；两条都**不**进 `impulseBubbles`（气泡流已删）。
- `chatStore.loadHistory`/`toChatMessage`（`tests/stores.test.ts`）：`HISTORY_TYPES` 含 `reading_question`/`reading_association`；`toChatMessage` 把 question 的 `content.content`、association 的 `content.snippet` 映射对（含 `subtype`/`selected_text`/`memory_id` 回填）。
- `dispatch`（`tests/sse.test.ts`）：`reading_question`/`reading_association` → `addReadingTurn`；`reading_mutter`/`mutter` → `announceStore.announce("mutter")`；`reflection_done` → `announce("mutter")`（保持）；不再有 `addReadingBubble`/`addMutter` 调用。
- 真分页纯函数 `paginate`（`tests/stores.test.ts`）：长段独占一页、短段一页多段、溢出封页（累加将超 viewport 即开新页）、空 `paragraphs`/`viewportHeight<=0` 返回 `[]`、`measureHeight` 含 `GAP_PX` 后页界正确。
- `MessageBubble`（组件测试，如无则 `readerView.test.tsx` 增补）：`reading_question` 渲染「提问」徽标 + 即时全量（不逐字，`displayed===content`）；`selectedText` 非空渲染引文行；`reading_association` 渲染「联想」徽标 + `memoryId` 存在渲染「记忆」标。
- `readerStore`：删 `addReadingBubble` 相关断言；`syncPosition` 页首段路径保持（前翻逐段补发、后翻不评估）。

## 完成定义

- [ ] `npx vitest run` 全绿、`npx tsc --noEmit` 零报错
- [ ] `test-inventory.md` 已更新（快照）
- [ ] 手动：左栏常驻对话（读书/面板都能聊）；读书真分页不滚动、高亮/🦊 标记不漂、`fontScale`/resize/换书重测后位置不丢；可拖拽头像圆圈（白底、可换底色、拖到窗口任意处、重启后位置/底色记住）、戳圆圈冒害羞短语、红点可点清除、拖拽不误触戳；碎碎念/读书碎碎念/反思浮泡从圆圈头顶冒出、几秒淡出；读书提问/联想进对话、刷新后仍在（历史回填）、回复能接上
