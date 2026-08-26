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
// useSSE 只挂一次；点左面板摘要 / 底部工具条替换书卷区视图；Ctrl+Shift+D 切调试页（eval+token）。
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
            <InnerWorld key={view} categoryIndex={view} />
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
