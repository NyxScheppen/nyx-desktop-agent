# 08 书页式完整窗口（聊天与阅读抽屉 + 真分页 + 桌宠窗口）

> 完整窗口采用「双开本 / 编年史」两套硬边羊皮纸外观：左侧图标导航切换页面，聊天右侧可拉开阅读抽屉。桌宠小窗的头像、菜单和陪读交互保留；完整窗口通过左侧导航底部印章按钮返回桌宠。读书提问与联想继续并进同一聊天记录。
> 对齐后端：`11-expression`（读书提问/联想进表达 history，回复可引用）。

> **行号是定位锚，不是指令**：本文行号只用于快速定位；落点以**符号名 + 变量名**为准。
> 桌宠模式的交互补充见 §8；Tauri 窗口行为由 `frontend/src/lib/desktopWindow.ts` 与 `src-tauri/tauri.conf.json` 实现。

## 1. 布局重构（App 装配 + 复用既有组件，不造新组件）

`App` 装配顶格左侧导航和主页面，不保留原型预览顶栏。默认进入聊天，`MessageList`、`ChatInput` 复用原来的 store 和 API；知识投喂入口收在记忆页的知识库内。完整端不挂载浮动头像或独立状态条。

```
┌──────┬──────────────────────┬───────────────────┐
│ 导航 │ 聊天记录 / 输入框      │ 阅读抽屉（可收起） │
│ 图标 │          30%          │       70%         │
│      ├──────────────────────┴───────────────────┤
│      │ 或：内在 / 欲望 / 活动 / 任务 / 创作等页面 │
└──────┴──────────────────────────────────────────┘
```

- **栅格**：主区高度受窗口约束，使用 `minmax(0,1fr)` 和可收缩的 flex 子项。阅读抽屉与聊天按阅读/聊天 `7:3` 分配；聊天内滚动，输入框保持可达；阅读抽屉宽度过渡时重新测量分页。
- **导航**：`RightDock` 保留文件名，展示左侧图标入口，以 title/aria-label 标识聊天、内在、欲望、活动、任务、创作、记忆、评估、历史和设置；知识库在记忆页右上角切换，Tauri 完整窗口底部印章用于收回桌宠。
- **阅读**：聊天卡片外的上方按钮展开/收起右侧抽屉，并显示书页展开动效；未选书时显示书架与 EPUB 导入，选书后显示 `ReaderView`。关闭抽屉不清空阅读进度。
- **页面**：内在左上显示心情图，右侧按精力、性格、审美、三观堆叠，四块等宽并保留数值；欲望显示顶部压力横条、搜索和期限/类别筛选；记忆、历史、评估和任务使用五条分页；普通记忆在分页与空态判断前排除知识类别，知识库列表只展示知识类别。知识库复用真实知识记忆与知识事实图，内部独立投喂页提供文本和 URL 摄入，未提供通用文件上传接口；活动提供历史日期选择，创作采用书架，任务记录与安排任务分开，网页/书籍/网页问卷三种表单上下排列为三排，每排占满宽度。
- **外观**：只在完整窗口应用两套书页布局；双开本采用红棕导航（`#6c342c`）、暖羊皮纸（`#f6e8c9`）和相连书脊，编年史采用深绿导航（`#223633`）、浅羊皮纸（`#ede2c0`）、横线纹理及 18px 页间隙，风格仅从设置切换；隐藏滚动条但保留滚动和键盘操作，页面切换及阅读翻页使用短暂动效，并遵循 `prefers-reduced-motion`。
- **背景层**：主题底纹统一由 `.app-bg` 绘制，聊天和模块页外层保持透明，书页面板保留纸张背景。自定义图片或色调覆盖默认底纹，图与色调并存时保留半透明滤镜；夜间遮罩作用于背景层，桌宠模式隐藏这些背景层。
- **设置**：完整窗口中作为左侧导航的普通页面展示，桌宠和角色尚未就绪时保留弹层入口；左侧窄栏外观、右侧角色管理；内置 Nyx 的初始人设直接展示 `nyx/cards/nyx/canon.md`，不使用原型占位文案。导入、创建、卸载、恢复和重试继续走已有角色接口。

### 角色切换补充

桌宠菜单提供 `CharacterSelect`；完整端从设置的 `CharacterManager` 管理和选择角色，完整窗口设置页（桌宠中为设置弹层）提供角色管理和四角色额度、状态、导入/卸载/恢复/重试。名称及头像来自角色卡，没有图片使用名称占位；全局字体、背景与窗口外观沿用 `settingsStore`。

领域 Store 只展示当前角色；切换清理旧展示、等待、通知和阅读临时缓冲，恢复按角色保存的草稿与最近书籍。全部异步结果核对角色及本地切换代数，覆盖 A→B→A；具体契约见 [14-character-cards](../specs/14-character-cards.md)、[Store](02-stores.md) 与 [API client](05-client.md)。

### 1.1 共享时钟与昼夜视觉

- `App` 持有唯一的本地 `Date` 时钟：首次 timeout 对齐下一分钟边界，之后每分钟更新，
  focus/恢复可见时立即校时并重新对齐分钟，卸载时清理 timeout 与监听器。完整端不额外显示顶栏时钟；时钟用于消息时间分隔和昼夜相位，时间纯函数签名见
  [聊天面板契约](03-chat-panel.md)。不新增依赖、配置或 Zustand store。
- 根节点设置 `data-time-phase="day|night"`；同一个时钟作为 `MessageList.now`，同一昼夜
  相位作为桌宠 `Avatar.night`。本地 06:00/22:00 自动切换，不等待 SSE、用户输入或状态刷新。
- 夜间通过 CSS 变量与半透明遮罩改变背景、面板、边框和文本对比，不替换用户自定义图片
  或色调，不增加影响布局尺寸的昼夜元素；只改变显示，不修改后端情绪、精力或活动数值。
- 前后端在同一台电脑运行，共用系统本地时区；不处理远程服务时区分离。

## 2. 读书反应并进对话（类型扩展 + dispatch 重路由 + 渲染契约）

表达系统（11-expression）不改事件（仍发 `READING_QUESTION`/`READING_ASSOCIATION`/`READING_MUTTER`），前端**重路由**进 `chatStore`。

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
  timestamp: number;
  preloaded?: boolean;
  // 读书 turn 专属（kind==="reading_question" 才有 subtype/selectedText；"reading_association" 才有 memoryId）
  subtype?: QuestionSubtype;
  selectedText?: string | null;
  memoryId?: string;
};
```

- `QuestionSubtype` 从 `types/api.ts` import（已存在，零映射）。
- 所有聊天消息保留后端 `timestamp`；实时复制 SSE，历史复制事件日志，不以接收时刻代替。

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
- `AnnounceLayer` 在桌宠态继续跟随头像，完整端挂载在右下角独立提示层。`ANNOUNCE_DURATION.mutter = 4000` 与 `announceStore` 的事件处理不变。

## 4. 可拖拽头像圆圈（白底可换底色 + 拖拽 + 头顶气泡）

`Avatar` 只在桌宠小窗挂载：`EmotionSprite` 裁进圆圈，位置/底色/尺寸存 localStorage；碎碎念气泡跟随头像。完整端不放浮动头像，避免覆盖抽屉工具。

- **结构**：`App` 在桌宠模式挂载 `PetShell`，由其组合 Avatar 和轻量菜单；完整端在右下角独立提示层挂载 `AnnounceLayer`，设置入口位于左侧导航。
- **昼夜输入**：`Avatar` 必须接收 `night: boolean`，不在 render 中自行读取 `new Date()`。
  夜间默认显示 sleepy，白天显示当前情绪；默认显示不写回 innerLifeStore。
- **`Avatar.tsx` 重写**：白底圆形（`backgroundColor = settingsStore.circleColor`，默认 `#ffffff`）；`position:fixed; right:24px; bottom:24px; border-radius:50%`，`width/height` 内联自 `settingsStore.circleSize` 三档（`CIRCLE_SIZES`：小 96 / 中 120 / 大 144，默认大），内层 `.avatar-circle__face` `overflow:hidden` + `EmotionSprite size="circle"`（`object-fit:cover` 方形表情图撑满不裁，图源 `assets/expressions/`）。位置记忆 `avatarPos` 非 null 时内联 `left/top` 覆盖默认右下角。表情图 `<img>` 加 `draggable={false}`、`.emotion-sprite--circle` 加 `pointer-events:none`，防浏览器原生图片拖拽抢占圆圈拖拽。
- **拖拽**：`onPointerDown/Move/Up/Cancel` + `setPointerCapture`；浏览器回退时 `getBoundingClientRect()` 记录起点，位移超 `DRAG_THRESHOLD=3` 判定为拖拽；拖拽中本地 `dragPos` 渲染、松手才 `setAvatarPos` 提交（一次 localStorage 写）。只有桌宠层显式传入 `useNativeWindowDrag` 且运行于 Tauri 时才调用原生 `startDragging()`；完整端不挂载头像。`clampAvatarPos` 把浏览器回退坐标夹回视口内。
- **桌宠入口**：单击头像由 `PetShell` 打开半月菜单；双击头像切换完整桌面端。红点通知（`.avatar-notice`）仍为纯红点（`aria-label="小狐狸我有话对你说"`），点击 `stopPropagation` 清除 `unreadProactive`。
- **持久化**：`settingsStore` 新增 `circleColor`/`circleSize`/`avatarPos` + 对应 setter，读写 localStorage（键 `nyx.circleColor`/`nyx.circleSize`/`nyx.avatarPos`），不可用时静默降级；`reset()` 一并清这三个键。`EmotionSprite` 的 `size` 变体删 `portrait`、增 `circle`（`portrait` 无调用方，清 orphan）。
- **设置项**：`SettingsView` 新增「圆圈背景」面板（预设白/浅粉/浅蓝/浅绿/浅橙/淡紫 + 自定义取色），复用 `.bg-panel` 色块；预设名与「背景色调」预设（樱粉/晨蓝/…）错开，避免测试按名选色歧义。另新增「圆圈大小」三档（小/中/大，复用 `.font-scale__opt` 按钮，aria-label 用「圆圈小/中/大」与字体三档错开）。

## 5. 真分页（页级取消滚动，纯函数分页 + 测量/重测契约）

`ReaderView` 由「整页滚动容器 + `onScroll`/`offsetTop`/`scrollBy`」改为**真分页**：页容器 `overflow:hidden`，翻页逐段移动光标、越页界整页切换；只有单段本身高于视口时，该段内部提供纵向滚动以保证全文可达。

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

- **量哪个元素**：每段 `<article>` 的 `offsetHeight`（内部可含标题、正文、引用、列表和 pre）。用现有 `paraRefs`（`ref` 回填 `Map<number, HTMLElement>`，键 = `p.index`）。单段设置视口内 `max-height` 与 `overflow-y:auto`，分页测到的是稳定的可视高度。
- **视口**：`.reader-text` 容器的 `clientHeight` 和 `clientWidth` 均由 `ResizeObserver` 维护为组件 state。
- **重测时机**：`useLayoutEffect` 依赖正文、笔记、字号、视口高度、视口宽度和段落窗口起点——任一变化重测全部段高并重新分页；高度不变但宽度收窄时也不能沿用旧页数。

### 5.3 状态归属（持久态 vs 瞬态）

- **持久态（`readerStore`）**：`userPosition` = **读到第 xx 段**（逐段移动的光标位置，非页首段），唯一落库（`putProgress` 已有）。不新增 store 字段。
- **瞬态（`ReaderView` 组件 state）**：当前页、实测段高、分页结果、视口宽高和翻页动效状态。这些 DOM 派生值不入 store。
- **`pageIndex` 从 `userPosition` 反推**：`pageIndex = pages.findIndex(p => p.includes(userPosition))`，找不到 → `0`；再 `clamp [0, pages.length-1]`。`userPosition` 是唯一事实来源，重分页后靠它找回页序，不丢位置。

### 5.4 翻页映射

- `上一页/下一页` 按钮不再调 `scrollBy`，改 `syncPosition(userPosition ± 1)`（逐段移动，clamp `[1, total]` 由 `syncPosition` 处理）。
- 光标（`--current` 高亮 =「你读到第 xx 段」）随 `userPosition` 逐段移动；`pageIndex` 从 `userPosition` 反推（§5.3），光标越页界时整页自动切换。复用既有 `syncPosition` 的「前翻逐段补发 `evaluateImpulse` + `putProgress` + 必要时 `fetchWindow` + `startCatchup`」整条管线。
- 视觉切换：内容 wrapper `transform: translateY(-页前累计高度)`（`-sum(measureHeight(前页各段))`，CSS `transition` 平滑），`overflow:hidden` 裁掉其余页；无滚动条、无滚轮。高亮 `--current`（`userPosition` 段）+ 🦊 `--nyx`（`nyxPosition` 段）保留（06 既有 class）。

### 5.5 窗口刷新不漂

- `syncPosition` 内 `needsWindowRefresh` → `fetchWindow(centered=false)`（既有，**不改**），重拉后 `windowFrom = userPosition`、新窗口首段即当前读到段。
- `windowFrom` 变化触发重分页（§5.2），`pageIndex` 归 `0`（新窗口从当前段开始，`userPosition` 即窗口首段）——**页状态不漂**。

### 5.6 删除项

- 删 `ReaderView` 的 `containerRef` 滚动逻辑：`handleScroll`（`onScroll`+`offsetTop`+rAF）、`page()` 的 `scrollBy`、`.reader-text` 的 `onScroll`。`useEffect` 里的 `scrollTop=0` 改为「重分页后 `pageIndex` 归位」逻辑。
- `ReaderSidebar` 拆解（§6 的 06 反向修订）：侧栏删除，内容四散——追赶进度 → 读书 header 一行「她读到第 K 段 / 你读到第 M 段」；冲动气泡 → §2/§3；「笔记」入口 → 读书 footer `重读` 旁（`NotePanel` 本体不动）。

## 6. 对既有前端 spec 的反向修订

- `01-sse.md`：分发表 `reading_question`/`reading_association` 改路由 `chatStore.addReadingTurn`；`reading_mutter` → `announceStore`；`mutter` → `announceStore`（原 mutterStore）；`reflection_done` 保持 `announce("mutter")`。
- `02-stores.md`：`chatStore` 增 `addReadingTurn` + `ChatMessage` 扩 `reading_*` kind 与 `subtype/selectedText/memoryId`；`readerStore` 删 `impulseBubbles`/`addReadingBubble`/`ReadingBubble`/`ReadingBubbleKind`；`mutterStore` 条目删除。
- `03-chat-panel.md`：聊天主区独立成页，右侧按需展开阅读抽屉；`ScrollArea` 删除；`MessageBubble.isNyxText` 与 `MessageList.NYX_TEXT_KINDS` 明确**不加** reading 两 kind（即时渲染）。
- `06-reading-panel.md`：§5「滚动容器 + onScroll」改「真分页」（`paginate` + 测量/重测/翻页契约）；`ReaderSidebar` 拆解（进度→header、气泡迁走、笔记→footer）；`readerStore` 删 `turnPage`（本 spec 已由 `syncPosition` 语义承载，§5.4 直接调 `syncPosition`）。
- `07-reading-events.md`：§2「冲动气泡（ReaderSidebar）」改「并进对话/悬浮气泡」。
- `types/api.ts`：**不改**（`ReadingQuestionEvent`/`ReadingAssociationEvent`/`ReadingMutterEvent`/`QuestionSubtype` 已存在；`ChatMessage` 在 `chatStore.ts` 非此文件）。

## 7. 测试要点

- `chatStore.addReadingTurn`（`tests/stores.test.ts`）：question → `kind==="reading_question"`、`subtype`/`selectedText` 落对、`correlation_id===book_id`；association → `kind==="reading_association"`、`memoryId`/`snippet`(→content) 落对；两条都**不**进 `impulseBubbles`（气泡流已删）。
- `chatStore.loadHistory`/`toChatMessage`（`tests/stores.test.ts`）：`HISTORY_TYPES` 含 `reading_question`/`reading_association`；`toChatMessage` 把 question 的 `content.content`、association 的 `content.snippet` 映射对（含 `subtype`/`selected_text`/`memory_id` 回填）。
- `dispatch`（`tests/sse.test.ts`）：`reading_question`/`reading_association` → `addReadingTurn`；`reading_mutter`/`mutter` → `announceStore.announce("mutter")`；`reflection_done` → `announce("mutter")`（保持）；不再有 `addReadingBubble`/`addMutter` 调用。
- 真分页纯函数 `paginate`（`tests/stores.test.ts`）：长段独占一页、短段一页多段、溢出封页（累加将超 viewport 即开新页）、空 `paragraphs`/`viewportHeight<=0` 返回 `[]`、`measureHeight` 含 `GAP_PX` 后页界正确。
- `MessageBubble`（组件测试，如无则 `readerView.test.tsx` 增补）：`reading_question` 渲染「提问」徽标 + 即时全量（不逐字，`displayed===content`）；`selectedText` 非空渲染引文行；`reading_association` 渲染「联想」徽标 + `memoryId` 存在渲染「记忆」标。
- `readerStore`：删 `addReadingBubble` 相关断言；`syncPosition` 逐段路径保持（前翻逐段补发、后翻不评估）。
- `tests/app.test.tsx`：fake timers 覆盖下一分钟更新及无需外部操作跨 06:00/22:00，根主题与
  Avatar 同相位；`tests/time.test.ts` 覆盖昼夜边界、时钟/消息标签及时间分隔。
  桌面和窄窗口人工检查导航、阅读按钮和时间分隔不遮挡，自定义背景在夜间遮罩下仍保留。

## 8. 桌宠模式（`components/desktop/PetShell.tsx`）

- Tauri 启动窗口为透明、无边框、普通非置顶的小窗口（`560×520`；其他应用激活时自然位于其后）；浏览器开发环境仍直接显示完整端，便于测试。菜单态头像水平居中，聊天/读书/内心等左展子面板打开时，交互层增加统一的 panel-open 标记并把头像改为距右侧 `24px`，保证最宽 `350px` 阅读面板完整留在窗口内；返回菜单后恢复居中。
- `App` 维护 `DesktopMode = "pet" | "full"`：桌宠态只渲染 `PetShell` 及其 Avatar；双击头像进入完整端，完整端左侧导航底部的「收回桌宠」印章按钮返回小窗。
- 桌宠菜单包含「聊天」「读书」「内心」「设置」四个入口，四项以头像中心为基准正东南西北排列；状态仍在头像头顶以被动摘要显示。
- 聊天小界面是无外框的一行 Galgame 对话 + 悬空输入框；读书先选书，再显示扁平主动对话气泡、正文、你/Nyx 段落标记和悬空输入框；选书页与陪读页都保留顶部无文字返回箭头 `→`。
- 陪读正文下方提供「上一页」「下一页」两个轻量按钮，调用 `readerStore.syncPosition(userPosition ± 1)` 逐段移动进度；到达首段或末段时禁用对应按钮，避免桌宠态只能读当前段而无法继续。正文区域保持 `148px` 高，单段过长时在区域内纵向滚动，既不裁掉原文，也不撑破固定桌宠窗口。
- 「内心」入口显示情绪、精力、当前活动和简短感受摘要；「设置」入口回调打开现有 `SettingsView`，直接修改 `settingsStore`，不复制设置写入逻辑。
- 桌宠小界面不复制业务逻辑：聊天复用 `chatStore.sendMessage`，读书复用 `readerStore.loadBooks/openBook` 与 `readerStore.syncPosition`；双击头像是唯一进入完整端的入口。
- 发布版 Tauri 由 Rust 壳启动同目录的 `nyx-backend` sidecar，并在 `127.0.0.1:8000` 可连接后完成 setup；日志和工作目录使用 Tauri app data，退出时关闭 sidecar stdin 触发 Python 优雅停止。Debug 版仍由 `dev.py --desktop` 启动后端，避免重复进程。
- `Avatar` 仅在收到 `useNativeWindowDrag` 且运行于 Tauri 时，在 pointer down 超过阈值后调用原生 `startDragging()`；桌宠层传入该标记，完整端不挂载 Avatar。浏览器回退到现有 CSS 拖拽和 `avatarPos` 持久化。

## 完成定义

- [ ] `npx vitest run` 全绿、`npx tsc --noEmit` 零报错
- [ ] `test-inventory.md` 已更新（快照）
- [ ] 手动：桌宠态透明无边框且使用普通非置顶层级；头像可拖动整个窗口；单击展开半月菜单、双击切换完整端；聊天浮框/输入框、选书/陪读面板、读书上方主动气泡和返回箭头位置正确；完整端继续复用现有聊天/阅读/状态逻辑；桌宠与完整端切换不丢聊天或阅读状态
