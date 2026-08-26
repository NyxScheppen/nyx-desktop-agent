# 游戏壳（三区书卷风布局 + 遭遇渲染）

> 范围：把现有「仪表盘 / 抽屉」形态改造成「养成游戏」形态——**三区布局**（左侧面板 30% + 书卷区域 + Galgame 对话框）+ **书卷风**（羊皮纸 + 衬线 + 暖棕，摒弃粉渐变 / 樱花）+ **遭遇渲染**（`ENCOUNTER_START` 选项卡片 → `POST /api/encounter/choose` → `ENCOUNTER_END` 结局上屏）。
> 后端契约见 `docs/specs/19-encounter.md`（本 spec 只消费其事件 + 端点，不改后端）。设计决策见 `docs/design/raising-sim.md` §5。
> 本文件自包含：新文件内联完整代码，改动文件给增量。

## 1. 目标与范围

三块（design §5.1 / §5.2）：

1. **三区布局**：左面板（主人公信息 + 心情/精力 + 欲望一句话 + 活动一条）｜书卷区域（对话主舞台）｜Galgame 对话框（输入 + 发送）。空间 / 记录 / 出门 / 游戏设置移到右侧底部工具条（输入框上方，六词条）。
2. **书卷风重构**：`index.css` 全量重写为羊皮纸暖棕 + 衬线字 + 中世纪装饰边框；`Sakura` 移除；沿用现有 8 档 sprite（不重绘）。
3. **遭遇渲染**：新增 `encounterStore` + `EncounterCard`，接 `encounter_start/choice/end` 三个新 SSE 事件 + 两个新 REST 端点。

范围外 / 推迟（design §6）：陪读、硬等级、语音、computer use。活动中遭遇（19-encounter 触发点 3）后端未实现，本 spec 不涉及。

## 2. 三区布局（组件树）

```
App.tsx（重写装配）
├─ useSSE(dispatchEvent)            # 不变：SSE 只挂一次
├─ usePresence()                    # 不变：活跃度上报
├─ <header>                         # 标题 + 连接状态 + 调试快捷键（隐藏入口）
├─ <div class="game-shell">
│   ├─ <LeftPanel onOpenInner={…}/> # 左面板 30%（新）
│   └─ <div class="game-main">      # 右主区 70%（内容按 view 四向渲染，底两条常驻）
│       ├─ view=null：<ScrollArea />        # 对话主舞台
│       ├─ view="settings"：<SettingsView/> # 游戏设置页内面板（字体大小 + 背景外观）
│       ├─ view="explore"：<ExplorationMap/># 探索地图页内视图（工具条「出门」）
│       ├─ view=number：<InnerWorld/>       # 内心世界页内面板（横向子标签单分类 0/1/2）
│       ├─ <RightDock/>             # 底部工具条（常驻：聊天/内在/空间/记录/出门/游戏设置）
│       └─ <ChatInput/>             # Galgame 对话框（常驻）
├─ {debugOpen 时 <div class="debug-overlay"><EvalPanel/></div>}  # eval+token 独立调试页
└─ <AnnounceLayer />                # 复用：头像旁淡出气泡
```

- **现有面板去向（design §5.2）**：聊天 / 遭遇 / 成长时刻 → 书卷区对话主舞台（默认）；精力 / 情绪 / 性格 / 三观 → 左面板「心情」纯展示 + 「她现在的念头」点开内在详情；欲望 → 左面板「她现在的念头」一句话 + 点开队列；读书笔记 / 产出 / 资料 → 右底工具条「空间」点开空间详情；记忆 / 活动 → 左面板「正在做什么」一条 + 点开记录详情；字体大小 / 背景 → 右底工具条「游戏设置」；eval + token → 独立调试页（快捷键 `Ctrl+Shift+D` 切换，复用 `EvalPanel`）。
- **`ChatPanel` 移除**：其职责拆散——头部「内在/空间/记录」按钮 → 左面板/右底工具条「点开详情」；「设置」→ 右底工具条游戏设置；`MessageList` → 书卷区对话模式；`ChatInput` → Galgame 对话框。
- **`InnerWorld` 去弹窗 + 横向子标签单分类**：从可拖拽弹窗改为页内面板（替换书卷区显示），仍是「内在 / 空间 / 记录」三分类，触发入口为左面板摘要点击 + 右底工具条（`onOpenInner(categoryIndex)` 同签名）。传 `categoryIndex`（0 内在 / 1 空间 / 2 记录）顶部渲染横向子标签条（网页 tab 感），点标签切活动面板（默认激活第一项，无全分类总览）。左面板摘要与工具条同源分类：她现在的念头→内在(0)、正在做什么→记录(2)；工具条另有「空间(1)」入口。切回聊天统一走底部工具条「聊天」入口（不再有视图内「返回对话」按钮），底工具条 + 输入框在切视图时保持常驻。

## 3. 遭遇数据流

```
后端（19-encounter）
  ENCOUNTER_START   {encounter_id, kind, text, options:[{index,text}]}
      │ SSE
  encounterStore.onStart  → current 置位 → EncounterCard 渲染 [文本 + 可点选项]
      │ 用户点选项
  encounterStore.choose(encounter_id, option_index)
      │ POST /api/encounter/choose {encounter_id, option_index} → {encounter_id, chosen}
  ENCOUNTER_CHOICE  {encounter_id, option_index, option_text}   ← 无消费者（end 紧跟）
  ENCOUNTER_END     {encounter_id, kind, option_index, option_text, ending, consequences}
      │ SSE
  encounterStore.onEnd
      ├─ current 清空（选项卡片消失）
      ├─ chatStore.addEncounterEnding(e)  → ending 上聊天时间线（kind:"encounter"）
      └─ refresh：innerLifeStore.refreshState() + desireStore.refresh() + memoryStore.refresh()
           （后果改精力/情感/欲望值/成长记忆，重拉快照 → 左面板属性实时变化）
```

- **恢复**：进页面 `encounterStore.refresh()`（`GET /api/encounter/current`）恢复未决遭遇（刷新页面不丢正在进行的遭遇）；挂载时 App 层调一次。
- **欲望搭话 = 前端重分类**：`initiate_chat` 仍走 `chatStore.addInitiateChat`，只把 `MessageBubble` 的徽标文案从「搭话」改为「欲望搭话」（design §3.4 类型一，后端不动）。

## 4. 书卷风样式（`index.css` 重写）

- 摒弃：粉渐变默认背景、`Sakura` 樱花、现代玻璃面板（`backdrop-filter` 毛玻璃、圆角白底卡片）。
- 换用：羊皮纸暖棕 + 衬线字 + 中世纪装饰边框。**CSS token（单一来源，`:root` 定义）**：

```css
:root {
  --parchment: #f3e6c4;        /* 羊皮纸底 */
  --parchment-deep: #e8d3a3;   /* 深层纸（边框/阴影） */
  --ink: #5b4636;              /* 墨色正文 */
  --ink-soft: #8a7257;         /* 次级文字 */
  --accent: #8c5a3b;           /* 暖棕点缀（按钮/链接） */
  --gold: #b8922a;             /* 描金（标题/装饰） */
  --font-serif: Georgia, "Songti SC", "STSong", "Noto Serif SC", "SimSun", serif;
  --text-scale: 1;             /* 字体大小档位（settingsStore.fontScale 驱动） */
}
```

- **字体大小**：`settingsStore` 新增 `fontScale: "small" | "medium" | "large"`，映射 `--text-scale` 0.9 / 1.0 / 1.12；`App` 在 `.game-shell` 上以内联 `style={{ ["--text-scale" as any]: scale }}` 注入，`body`/`.game-shell` 用 `font-size: calc(1rem * var(--text-scale))` 统一缩放。
- **边框**：书卷区 / 左面板 / 对话框用 `2px double var(--gold)` 外框 + `box-shadow: inset 0 0 0 1px var(--parchment-deep)`，四角不加圆角（或小圆角 4px）；标题用描金下划线。
- **背景图/色调**：沿用 `settingsStore.tint/image`（design §5.1「背景切换」），但默认态从「粉渐变」改为羊皮纸 `--parchment`（`tint=null` 时 `body` 背景 `var(--parchment)`）。
- **樱花**：`Sakura.tsx` 删除，`App.tsx` 不再渲染 `<Sakura />`。

## 5. 新增文件（完整代码）

### 5.1 `src/stores/encounterStore.ts`

```typescript
import { create } from "zustand";
import { chooseEncounter, getCurrentEncounter } from "../api/client";
import { useChatStore } from "./chatStore";
import { useDesireStore } from "./desireStore";
import { useInnerLifeStore } from "./innerLifeStore";
import { useMemoryStore } from "./memoryStore";
import type {
  EncounterCurrent,
  EncounterEndEvent,
  EncounterStartEvent,
} from "../types/api";

// 遭遇（19-encounter）：ENCOUNTER_START 置位 current（EncounterCard 渲染），
// 用户选选项 POST /api/encounter/choose，ENCOUNTER_END 清位 + ending 上聊天
// 时间线 + 后果改属性（重拉内在/欲望/记忆快照）。
// SSE 主通道：choose 只 POST，不本地清 current（信任 encounter_end 随后到达）。
type EncounterState = {
  current: EncounterCurrent | null; // GET /api/encounter/current 或 encounter_start 置位；null = 无未决遭遇
  choosing: boolean;                // 选项点击后 POST 往返期间禁用（防连击）
  error: string | null;
  onStart: (e: EncounterStartEvent) => void;
  onEnd: (e: EncounterEndEvent) => void;
  choose: (encounterId: string, optionIndex: number) => Promise<void>;
  refresh: () => Promise<void>;     // 进页面恢复未决遭遇
  reset: () => void;
};

export const useEncounterStore = create<EncounterState>((set, get) => ({
  current: null,
  choosing: false,
  error: null,
  onStart: (e) => {
    set({
      current: {
        encounter_id: e.encounter_id,
        kind: e.kind,
        text: e.text,
        options: e.options,
      },
      choosing: false,
      error: null,
    });
  },
  onEnd: (e) => {
    set({ current: null, choosing: false });
    // 结局叙事上聊天时间线（kind:"encounter"）+ 后果改属性 → 重拉快照
    useChatStore.getState().addEncounterEnding(e);
    void useInnerLifeStore.getState().refreshState(); // energy/emotion 变了
    void useDesireStore.getState().refresh();         // 欲望值变了
    void useMemoryStore.getState().refresh();         // 成长时刻落记忆
  },
  choose: async (encounterId, optionIndex) => {
    const cur = get().current;
    if (cur === null || cur.encounter_id !== encounterId) return;
    set({ choosing: true, error: null });
    try {
      await chooseEncounter(encounterId, optionIndex);
      // encounter_end SSE 随后清 current + 上屏 ending；此处不提前清（SSE 主通道）
    } catch (err) {
      set({
        choosing: false,
        error: err instanceof Error ? err.message : String(err),
      });
    }
  },
  refresh: async () => {
    set({ error: null });
    try {
      const current = await getCurrentEncounter();
      set({ current, choosing: false });
    } catch (err) {
      set({ error: err instanceof Error ? err.message : String(err) });
    }
  },
  reset: () => set({ current: null, choosing: false, error: null }),
}));
```

### 5.2 `src/components/encounter/EncounterCard.tsx`

```tsx
import { useEncounterStore } from "../../stores/encounterStore";
import { ENCOUNTER_KIND_LABELS } from "../../lib/labels";

// 遭遇卡片（19-encounter / design §3.5）：ENCOUNTER_START 的文本 + 可点选项。
// 读 encounterStore.current，null 不渲染；选项点击 → choose()；choosing 期间禁用。
// 开场文本与选项只在卡片内，不上聊天历史（结局经 encounter_end 上屏，见 3）。
export default function EncounterCard() {
  const current = useEncounterStore((s) => s.current);
  const choosing = useEncounterStore((s) => s.choosing);
  const error = useEncounterStore((s) => s.error);
  const choose = useEncounterStore((s) => s.choose);

  if (current === null) return null;

  return (
    <div className="encounter-card">
      <span className="encounter-card__badge">
        {ENCOUNTER_KIND_LABELS[current.kind] ?? current.kind}
      </span>
      <p className="encounter-card__text">{current.text}</p>
      <div className="encounter-card__options">
        {current.options.map((o) => (
          <button
            key={o.index}
            type="button"
            className="encounter-card__option"
            disabled={choosing}
            onClick={() => void choose(current.encounter_id, o.index)}
          >
            {o.text}
          </button>
        ))}
      </div>
      {error !== null && <p className="error-text">{error}</p>}
    </div>
  );
}
```

### 5.3 `src/components/shell/ScrollArea.tsx`

```tsx
import { useChatStore } from "../../stores/chatStore";
import MessageList from "../chat/MessageList";
import EncounterCard from "../encounter/EncounterCard";

// 书卷区域（design §5.1）：对话主舞台（MessageList + EncounterCard 遭遇卡片）。
// 记忆/笔记/资料等观测面板统一走左面板摘要入口（InnerWorld），此处不再多模式切换。
export default function ScrollArea() {
  const messages = useChatStore((s) => s.messages);

  return (
    <section className="scroll-area">
      <div className="scroll-area__body">
        <MessageList messages={messages} />
        <EncounterCard />
      </div>
    </section>
  );
}
```

> 说明：`EncounterCard` 内部自读 `encounterStore`（不需要 ScrollArea 传参）；挂载时由 App 层 `encounterStore.refresh()` 恢复未决遭遇（不在此组件内，避免重复挂载刷新）。

### 5.4 `src/components/shell/LeftPanel.tsx`

```tsx
import { EMOTION_LABELS, DESIRE_TYPE_LABELS } from "../../lib/labels";
import { activityStatusText } from "../../lib/activityResult";
import { useInnerLifeStore } from "../../stores/innerLifeStore";
import { useDesireStore } from "../../stores/desireStore";
import { useActivityStore } from "../../stores/activityStore";
import Avatar from "../inner/Avatar";
import EnergyBar from "../inner/EnergyBar";

type LeftPanelProps = {
  onOpenInner: (categoryIndex: number) => void; // 点摘要 → 弹对应分类详情（复用 InnerWorld）
};

// 左面板（design §5.1，30%）：大头照 + 姓名 + 心情/精力（纯展示）+ 欲望一句话 + 活动一条。
// 内心世界入口一一对应：她现在的念头→内在(0)、正在做什么→记录(2)。
// 空间(1)/记录(2)/出门/游戏设置入口在右底工具条（RightDock），左面板只留内在(0)/记录(2)两条摘要直达。
export default function LeftPanel({ onOpenInner }: LeftPanelProps) {
  const current = useInnerLifeStore((s) => s.current);
  const desires = useDesireStore((s) => s.data);
  const activity = useActivityStore((s) => s.data?.current ?? null);

  // 「她现在的念头」：取最活跃的一条短期欲望（active 优先，否则按 strength 降序第一条）
  const activeDesire =
    desires?.short_term.find((d) => d.status === "active") ??
    desires?.short_term[0];

  return (
    <aside className="left-panel">
      <Avatar />
      <h2 className="left-panel__name">Nyx</h2>

      <div className="left-panel__summary left-panel__summary--static">
        <div className="left-panel__summary-line">
          <span className="left-panel__summary-label">心情</span>
          <span className="left-panel__summary-value">
            {current !== null ? EMOTION_LABELS[current.emotion] : "……"}
          </span>
        </div>
        {current !== null && (
          <EnergyBar energy={current.energy} energy_state={current.energy_state} />
        )}
      </div>

      <button
        type="button"
        className="left-panel__summary"
        onClick={() => onOpenInner(0)} // 内在分类（内在状态/欲望/叙事）
      >
        <span className="left-panel__summary-label">她现在的念头</span>
        <span className="left-panel__summary-value">
          {activeDesire !== undefined
            ? `${DESIRE_TYPE_LABELS[activeDesire.type]} · ${activeDesire.description}`
            : "此刻没有特别的念头"}
        </span>
      </button>

      <button
        type="button"
        className="left-panel__summary"
        onClick={() => onOpenInner(2)} // 记录分类（活动/记忆）
      >
        <span className="left-panel__summary-label">正在做什么</span>
        <span className="left-panel__summary-value">
          {activityStatusText(activity)}
        </span>
      </button>
    </aside>
  );
}
```

> `activityStatusText` 是新增的纯函数（见 6.7），原 `StatusBar` 内的 `statusText` 上移到 `lib/activityResult.ts` 共享（`StatusBar` 与 `LeftPanel` 同源）。`ENERGY_LABELS` 已在 EnergyBar 内使用，LeftPanel 不重复引。空间(1)/记录(2)/出门/游戏设置迁到 `RightDock`（见 5.5），`useSettingsStore` 引用随之从本组件移除。

### 5.5 `src/components/shell/RightDock.tsx`（新）

```tsx
// 书卷区当前视图：null = 对话主舞台；number = InnerWorld 分类（0 内在 / 1 空间 / 2 记录）；
// "settings" = 游戏设置页；"explore" = 出门探索地图。
export type View = number | "settings" | "explore" | null;

type RightDockProps = {
  view: View; // 当前视图，用于高亮激活入口
  onSwitch: (view: View) => void;
};

// 右侧底部工具条（输入框上方，常驻不随切视图消失）：聊天 / 内在 / 空间 / 记录 / 出门 / 游戏设置。
// 六个入口替换书卷区内容（切视图），当前入口高亮；字体大小/背景设置已迁至 SettingsView。
// 动作类词条（出门，将来的一起读书）与观测面板同一条，未来加词条只需往 ENTRIES 追加一项。
const ENTRIES: readonly { label: string; view: View }[] = [
  { label: "聊天", view: null },
  { label: "内在", view: 0 },
  { label: "空间", view: 1 },
  { label: "记录", view: 2 },
  { label: "出门", view: "explore" },
  { label: "游戏设置", view: "settings" },
];

export default function RightDock({ view, onSwitch }: RightDockProps) {
  return (
    <div className="right-dock">
      {ENTRIES.map((e) => (
        <button
          key={e.label}
          type="button"
          className={`right-dock__entry${
            view === e.view ? " right-dock__entry--active" : ""
          }`}
          aria-pressed={view === e.view}
          onClick={() => onSwitch(e.view)}
        >
          {e.label}
        </button>
      ))}
    </div>
  );
}
```

> `View` 由 `RightDock` 导出（`number | "settings" | "explore" | null`），`App` 复用之；`RightDock` 统一 `onSwitch(view)` 切视图，按 `view` 高亮当前入口（`aria-pressed` + `.right-dock__entry--active`）。底工具条常驻；「内在(0)」「记录(2)」与 `LeftPanel` 的 `onOpenInner` 同源分类（左面板摘要直达 + 工具条同词条）。「出门("explore")」挂载 `ExplorationMap` 页内视图。

### 5.6 `src/components/layout/SettingsView.tsx`（新）

```tsx
import { useSettingsStore } from "../../stores/settingsStore";
import BackgroundPanel from "../panels/BackgroundPanel";
import Panel from "./Panel";

// 游戏设置页内面板（替换书卷区）：字体大小 + 背景外观（色调/背景图）。
// 复用 BackgroundPanel（纯前端 settingsStore）；字体大小原在 RightDock 折叠条，随「切视图」迁入。
const FONT_OPTIONS = [
  ["small", "小"],
  ["medium", "中"],
  ["large", "大"],
] as const;

export default function SettingsView() {
  const fontScale = useSettingsStore((s) => s.fontScale);
  const setFontScale = useSettingsStore((s) => s.setFontScale);

  return (
    <section className="side-panel">
      <header className="side-panel__header">
        <span className="side-panel__title">游戏设置</span>
      </header>
      <div className="side-panel__body">
        <Panel title="字体大小">
          <div className="font-scale">
            {FONT_OPTIONS.map(([key, label]) => (
              <button
                key={key}
                type="button"
                className={`font-scale__opt${
                  fontScale === key ? " font-scale__opt--active" : ""
                }`}
                aria-pressed={fontScale === key}
                onClick={() => setFontScale(key)}
              >
                {label}
              </button>
            ))}
          </div>
        </Panel>
        <BackgroundPanel />
      </div>
    </section>
  );
}
```

> `SettingsView` 复用 `BackgroundPanel`（背景色调/背景图）与 `Panel`（通用面板容器）；`.side-panel` 容器样式与 `InnerWorld` 共用。

### 5.7 `src/App.tsx`（重写装配）

```tsx
import { useEffect, useState, type CSSProperties } from "react";
import { dispatchEvent } from "./api/dispatch";
import AnnounceLayer from "./components/AnnounceLayer";
import ChatInput from "./components/chat/ChatInput";
import ExplorationMap from "./components/exploration/ExplorationMap";
import InnerWorld from "./components/layout/InnerWorld";
import SettingsView from "./components/layout/SettingsView";
import LeftPanel from "./components/shell/LeftPanel";
import RightDock, { type View } from "./components/shell/RightDock";
import ScrollArea from "./components/shell/ScrollArea";
import EvalPanel from "./components/panels/EvalPanel";
import { usePresence } from "./hooks/usePresence";
import { useSSE } from "./hooks/useSSE";
import { useActivityStore } from "./stores/activityStore";
import { useEncounterStore } from "./stores/encounterStore";
import { useInnerLifeStore } from "./stores/innerLifeStore";
import { useSettingsStore } from "./stores/settingsStore";
import type { ConnectionState } from "./types/api";

const CONNECTION_LABEL: Record<ConnectionState, string> = {
  connecting: "重连中",
  open: "已连接",
  closed: "已断开",
};

// 字体大小档 → --text-scale 数值（04 书卷风 §4）。
const FONT_SCALE_VALUE: Record<"small" | "medium" | "large", number> = {
  small: 0.9,
  medium: 1,
  large: 1.12,
};

// 游戏壳装配（06-game-shell）：三区布局——左面板 + 书卷区域 + Galgame 对话框。
// useSSE 只挂一次；点左面板摘要/右底工具条替换书卷区显示；Ctrl+Shift+D 切调试页（eval+token）。
export default function App() {
  const status = useSSE(dispatchEvent);
  const refreshState = useInnerLifeStore((s) => s.refreshState);
  const refreshActivity = useActivityStore((s) => s.refresh);
  const refreshEncounter = useEncounterStore((s) => s.refresh);
  usePresence();
  // 书卷区当前视图：null = 对话主舞台；number = InnerWorld 分类（0 内在 / 1 空间 / 2 记录）；"settings" = 游戏设置页；"explore" = 出门探索
  const [view, setView] = useState<View>(null);
  const [debugOpen, setDebugOpen] = useState(false);
  const tint = useSettingsStore((s) => s.tint);
  const image = useSettingsStore((s) => s.image);
  const fontScale = useSettingsStore((s) => s.fontScale);

  // SSE 恢复连接后重拉快照 + 未决遭遇（断线期间 encounter_start/emotion_update 可能丢失）
  useEffect(() => {
    if (status === "open") {
      refreshState();
      void refreshActivity();
      void refreshEncounter();
    }
  }, [status, refreshState, refreshActivity, refreshEncounter]);

  // 调试页快捷键（隐藏入口）：Ctrl+Shift+D
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "D" && e.ctrlKey && e.shiftKey) {
        setDebugOpen((v) => !v);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  // 背景：有图以图铺底（cover）；无图有色调作纯色；默认羊皮纸（--parchment）。
  const bgStyle: CSSProperties = {};
  if (image !== null) {
    bgStyle.backgroundImage = `url(${image})`;
    bgStyle.backgroundSize = "cover";
    bgStyle.backgroundPosition = "center";
  } else if (tint !== null) {
    bgStyle.background = tint;
  }

  const shellStyle = {
    "--text-scale": FONT_SCALE_VALUE[fontScale],
  } as CSSProperties;

  return (
    <div className="app">
      <div className="app-bg" aria-hidden="true" style={bgStyle} />
      {tint !== null && image !== null && (
        <div className="app-bg-tint" aria-hidden="true" style={{ backgroundColor: tint }} />
      )}

      <header className="app-topbar">
        <span className="scene-title">✦ Nyx ✦</span>
        <div className="topbar-right">
          <span className="connection-state">{CONNECTION_LABEL[status]}</span>
        </div>
      </header>

      <main className="game-shell" style={shellStyle}>
        <LeftPanel onOpenInner={(i) => setView(i)} />
        <div className="game-main">
          {view === null ? (
            <ScrollArea />
          ) : view === "settings" ? (
            <SettingsView />
          ) : view === "explore" ? (
            <ExplorationMap />
          ) : (
            <InnerWorld categoryIndex={view} />
          )}
          <RightDock view={view} onSwitch={(v) => setView(v)} />
          <ChatInput />
        </div>
      </main>
      {debugOpen && (
        <div className="debug-overlay">
          <div className="debug-overlay__bar">
            <span>调试</span>
            <button type="button" onClick={() => setDebugOpen(false)}>
              关闭
            </button>
          </div>
          <EvalPanel />
        </div>
      )}
      <AnnounceLayer />
    </div>
  );
}
```

> `view` 单值四向：`null` 对话主舞台（ScrollArea）、`"settings"` 游戏设置页（SettingsView）、`"explore"` 探索地图（ExplorationMap）、`number` 内心世界页内面板（InnerWorld，0/1/2 竖排单分类）。`RightDock` 常驻并统一 `onSwitch={(v) => setView(v)}` 切视图（六词条：聊天/内在/空间/记录/出门/游戏设置，高亮当前）；`ChatInput` 也常驻，故切视图时底工具条 + 输入框都不消失。`LeftPanel` 仍传 `(i) => setView(i)` 打开内在(0)/记录(2)（与工具条同源分类）。`InnerWorld`/`SettingsView`/`ExplorationMap` 均无 `onBack`（切回聊天走工具条「聊天」）。`ChatPanel`/`Sakura` 不再引用。

## 6. 修改文件（增量）

### 6.1 `src/types/api.ts`（追加）

```typescript
// ---- 遭遇（19-encounter）----
export type EncounterKind = "desire_chat" | "random_event" | "growth_moment";

/** 遭遇选项（前端只收 {index, text}；tone 被后端 _start_content 剥掉，前端不消费）。 */
export type EncounterOption = { index: number; text: string };

/** 未决遭遇（GET /api/encounter/current 与 encounter_start 同形状）。 */
export type EncounterCurrent = {
  encounter_id: string;
  kind: EncounterKind;
  text: string;
  options: EncounterOption[];
};

export type EncounterStartEvent = SseBase & {
  event: "encounter_start";
  encounter_id: string;
  kind: EncounterKind;
  text: string;
  options: EncounterOption[];
};

export type EncounterChoiceEvent = SseBase & {
  event: "encounter_choice";
  encounter_id: string;
  option_index: number;
  option_text: string;
};

export type EncounterEndEvent = SseBase & {
  event: "encounter_end";
  encounter_id: string;
  kind: EncounterKind;
  option_index: number;
  option_text: string;
  ending: string;
  consequences: Record<string, unknown>; // 前端不读后果细节，只触发快照 refresh
};
```

并把 `SseEvent` 判别联合追加三个成员（`EncounterStartEvent | EncounterChoiceEvent | EncounterEndEvent`）。**不要**加进 `OpaqueEventType`（这三类有消费者）。

### 6.2 `src/hooks/useSSE.ts`（追加）

`EVENT_TYPES` 数组追加三个：

```typescript
  "encounter_start",
  "encounter_choice",
  "encounter_end",
```

### 6.3 `src/api/dispatch.ts`（追加路由）

```typescript
import { useEncounterStore } from "../stores/encounterStore";
// ...（现有 import 不动）

  case "encounter_start":
    useEncounterStore.getState().onStart(e);
    return;
  case "encounter_choice":
    // 无消费者：encounter_end 紧跟其后，由它清 current + 上屏 ending（6.4）。
    return;
  case "encounter_end":
    useEncounterStore.getState().onEnd(e);
    return;
```

### 6.4 `src/stores/chatStore.ts`（追加 kind + action）

`ChatMessage.kind` 追加 `"encounter"`：

```typescript
  kind: "message" | "speak" | "ask" | "think" | "mutter" | "initiate_chat" | "encounter";
```

`ChatState` 追加 action 并在 store 实现：

```typescript
  addEncounterEnding: (e: EncounterEndEvent) => void;
```

```typescript
    // 遭遇结局叙事（19-encounter）：ending 作为 nyx 文本上时间线，即时全量（不逐字）。
    addEncounterEnding: (e) => {
      const msg: ChatMessage = {
        id: e.event_id,
        role: "nyx",
        kind: "encounter",
        content: e.ending,
        correlation_id: e.correlation_id,
      };
      set((s) => ({ messages: [...s.messages, msg] }));
    },
```

（`EncounterEndEvent` 加入 `types/api` 的 import。）

### 6.5 `src/components/chat/MessageBubble.tsx`（追加 kind 渲染）

- `isNyxText` **不**加 `"encounter"`（结局即时全量，不逐字，不进 `NYX_TEXT_KINDS`）。
- 渲染分支：kind `"encounter"` 与 `speak` 同款左气泡，加徽标 `遭遇`（与 `initiate_chat` 徽标并列；其 `initiate_chat` 徽标文案由「搭话」改为「欲望搭话」，见 design §3.4）：

```tsx
      {kind === "initiate_chat" && <span className="message-bubble__badge">欲望搭话</span>}
      {kind === "encounter" && <span className="message-bubble__badge">遭遇</span>}
```

### 6.6 `src/api/client.ts`（追加两个端点）

```typescript
export async function chooseEncounter(
  encounterId: string,
  optionIndex: number,
): Promise<{ encounter_id: string; chosen: number }> {
  return request<{ encounter_id: string; chosen: number }>(
    `${BASE_URL}/api/encounter/choose`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ encounter_id: encounterId, option_index: optionIndex }),
    },
  );
}

export async function getCurrentEncounter(): Promise<EncounterCurrent | null> {
  return request<EncounterCurrent | null>(`${BASE_URL}/api/encounter/current`);
}
```

（`EncounterCurrent` 加入 `types/api` import。）

### 6.7 `src/lib/activityResult.ts` + `StatusBar.tsx`（活动文字共享）

`activityResult.ts` 追加（从 `StatusBar` 上移的纯函数，LeftPanel 与 StatusBar 共用）：

```typescript
/** 活动状态一句话（读当前活动）：「在读《X》/在探索/在创作/在观察你/在静默反思/在休息/空闲」。 */
export function activityStatusText(a: Activity | null): string {
  if (a === null) return "空闲";
  const subject = activitySubject(a);
  switch (a.type) {
    case "reading":
      return subject !== null ? `在读《${subject}》` : "在读书";
    case "free_exploration":
      return subject !== null ? `在探索「${subject}」` : "在探索";
    case "creation":
      return subject !== null ? `在创作：${subject}` : "在创作";
    case "observe_user":
      return "在观察你";
    case "idle_reflection":
      return "在静默反思";
    case "rest":
      return "在休息";
  }
}
```

`StatusBar.tsx` 改为委托（删除本地 `statusText`）：

```tsx
import { activityStatusText } from "../lib/activityResult";
import { useActivityStore } from "../stores/activityStore";

export default function StatusBar() {
  const current = useActivityStore((s) => s.data?.current ?? null);
  return <div className="status-bar">{activityStatusText(current)}</div>;
}
```

> `StatusBar` 保留（仍作底栏活动文字，或由左面板「正在做什么」替代后移除——实现时二选一，见 7 验收「不冗余」）。本 spec 以「保留 StatusBar 并委托」为最小改动；若左面板已覆盖活动展示，可删 `StatusBar`（并删 `App` 引用 + `.status-bar` 样式）。

### 6.8 `src/stores/settingsStore.ts`（追加 fontScale）

```typescript
type FontScale = "small" | "medium" | "large";

type SettingsState = {
  tint: string | null;
  image: string | null;
  fontScale: FontScale;
  setTint: (tint: string | null) => void;
  setImage: (image: string | null) => void;
  setFontScale: (fontScale: FontScale) => void;
  reset: () => void;
};

export const useSettingsStore = create<SettingsState>((set) => ({
  tint: null,
  image: null,
  fontScale: "medium",
  setTint: (tint) => set({ tint }),
  setImage: (image) => set({ image }),
  setFontScale: (fontScale) => set({ fontScale }),
  reset: () => set({ tint: null, image: null, fontScale: "medium" }),
}));
```

### 6.9 `src/lib/labels.ts`（追加 EncounterKind 标签）

```typescript
export const ENCOUNTER_KIND_LABELS: Record<EncounterKind, string> = {
  desire_chat: "欲望搭话",
  random_event: "随机事件",
  growth_moment: "成长时刻",
};
```

（`EncounterKind` 加入 `types/api` import。）

### 6.10 删除文件

- `src/components/scene/Sakura.tsx`（樱花装饰，design §5.3 摒弃）
- `src/components/chat/ChatPanel.tsx`（职责拆散到 ScrollArea / LeftPanel / RightDock / ChatInput）
- `src/components/layout/DraggablePanel.tsx`（可拖拽弹窗骨架，InnerWorld 去弹窗后无调用方）
- `src/components/layout/SidePanel.tsx`（设置标签页面板，职责被 SettingsView 接管）
- `src/hooks/useDraggable.ts`（可拖拽 hook，DraggablePanel 删除后无调用方）

## 7. 验收标准

- [ ] 三区布局落地：`.game-shell` 左 30% + 右 70%，右区上「书卷区域」下「Galgame 对话框」；`App.tsx` 装配，`useSSE`/`usePresence` 只挂一次
- [ ] 书卷区对话主舞台：时间线滚动 + 遭遇卡片（单一视图，无模式切换按钮）
- [ ] 左面板：大头照 + 姓名 + 心情（情绪 + 精力条，纯展示）+ 她现在的念头（一句话，点开内在）+ 正在做什么（一条，点开记录）
- [ ] 右侧底部工具条（输入框上方，常驻）六词条：聊天（切回对话）+ 内在(0) + 空间(1) + 记录(2) + 出门（探索地图）+ 游戏设置（页内面板：字体大小 + 背景色调/背景图）
- [ ] 点左面板摘要 / 右底工具条替换书卷区显示，每个词条打开只切一次页面：`InnerWorld` 竖排单分类（0 内在 / 1 空间 / 2 记录，整类面板竖排滚动，无子标签、无全分类总览）；「出门」显示 `ExplorationMap`；「游戏设置」显示字体大小 + 背景外观；底部工具条 + 输入框在切视图时保持常驻，切回聊天走工具条「聊天」入口（当前入口高亮）；`Ctrl+Shift+D` 切调试页（eval+token，复用 `EvalPanel`）
- [ ] 书卷风：羊皮纸暖棕 + 衬线字 + 描金装饰边框；粉渐变默认背景 → 羊皮纸；`Sakura` 移除；沿用 8 档 sprite
- [ ] `encounter_start` → `EncounterCard` 渲染 `{text, options}`；点选项 → `POST /api/encounter/choose`；`encounter_end` → 卡片消失 + `ending` 上聊天时间线 + 重拉内在/欲望/记忆快照
- [ ] `encounter_choice` 无消费者（end 紧跟）；三个事件都进 `EVENT_TYPES` + `types/api.ts` 判别联合（漏加会被浏览器静默丢弃）
- [ ] 进页面 `encounterStore.refresh()` 恢复未决遭遇；`initiate_chat` 徽标改为「欲望搭话」
- [ ] 字体大小档位驱动 `--text-scale`（0.9/1.0/1.12），`settingsStore.fontScale` 持久于会话内（重启回默认，MVP）
- [ ] `tsc --noEmit` 严格零报错；`vitest run` 全绿

## 8. 测试要点

- **`encounterStore`**（`tests/stores.test.ts` 追加）：`onStart` 置 `current`（encounter_id/kind/text/options 零映射）；`onEnd` 清 `current` + 调 `chatStore.addEncounterEnding` + `innerLifeStore.refreshState`/`desireStore.refresh`/`memoryStore.refresh`（mock 三个 store 断言被调）；`choose` mock fetch 断言 `POST /api/encounter/choose` body `{encounter_id, option_index}`、成功不本地清 current、失败置 `error` + `choosing=false`；`refresh` mock fetch 断言 `GET /api/encounter/current` → `current` 落 store（含 `null` 分支）。
- **`chatStore.addEncounterEnding`**：断言 `ending` 转 `ChatMessage{role:"nyx", kind:"encounter", content, correlation_id}` 并 append；不改 `isReplying`/`pendingId`。
- **`dispatchEvent`**（`tests/sse.test.ts` 追加）：`encounter_start` → `encounterStore.onStart`；`encounter_end` → `encounterStore.onEnd`；`encounter_choice` 无副作用不崩。
- **`client`**（`tests/api.test.ts` 追加）：`chooseEncounter` 请求 `POST /api/encounter/choose` body `{encounter_id, option_index}` → `{encounter_id, chosen}` 解析正确；`getCurrentEncounter` `GET /api/encounter/current` → `EncounterCurrent | null` 解析正确（含 `null`）。
- **`activityStatusText`**（`tests/activityResult.test.ts` 追加）：`null` → 空闲；reading 带 subject → `在读《X》`；creation/exploration/observe_user/idle_reflection/rest 各分支文案正确。
- **`labels`**（`tests/labels.test.ts` 追加）：`ENCOUNTER_KIND_LABELS` 三键中文映射正确；`label()` 未知键回退。
- 组件级：`EncounterCard` 渲染文本 + 选项按钮、点击调 `choose`、`choosing` 禁用（React Testing Library，mock store）；`ScrollArea` 渲染 `MessageList` + `EncounterCard`（不再模式切换）；`InnerWorld` 页内面板横向子标签单分类（`categoryIndex` 0/1/2 各渲染该类子标签条，点标签切活动面板、默认激活第一项，无全分类总览）；`RightDock` 渲染六入口（聊天/内在/空间/记录/出门/游戏设置）+ 按 `view` 高亮当前（`aria-pressed`）+ 点入口触发 `onSwitch(null/0/1/2/"explore"/"settings")`；`ExplorationMap` 渲染 HUD + 节点 + 安全房，点节点/下楼/撤退调 choose，无 decision 显示出门探索，展开地图显示足迹；`SettingsView` 渲染字体大小三档 + 背景外观，点档位写 `fontScale`、点色块写 `tint`。视觉样式不做断言（README §6）。

## 9. 文档同步

- `docs/frontend/README.md`：目录结构补 `encounterStore`/`EncounterCard`/`shell/{LeftPanel,ScrollArea}`；「面板去向」表更新为三区布局映射；删 `ChatPanel`/`Sakura` 条目。
- `docs/frontend/01-sse.md`：`EVENT_TYPES`（21→24）+ 分发表加三行（`encounter_start/choice/end`）+ `types/api.ts` 判别联合追加。
- `docs/frontend/02-stores.md`：`encounterStore` state 形状 + actions；`chatStore` 加 `kind:"encounter"` + `addEncounterEnding`；`settingsStore` 加 `fontScale`。
- `docs/frontend/05-client.md`：端点函数清单 22→24（`chooseEncounter`/`getCurrentEncounter`）+ 测试要点。
- `docs/frontend/03-chat-panel.md`：标注 `ChatPanel` 已拆散（职责迁移到 06-game-shell），MessageBubble 加 `encounter`/「欲望搭话」徽标。
- `docs/specs/18-api.md`：端点计数 15→17（与 19-encounter 后端对齐），前端 client 镜像 2 新端点。
- `docs/test-inventory.md`：按系统/方向/阶段追加 encounter store/client/dispatch/labels/activityResult 测试（见 CLAUDE.md Part 3）。
