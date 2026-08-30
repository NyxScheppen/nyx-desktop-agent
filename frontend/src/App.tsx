import { useEffect, useState, type CSSProperties } from "react";
import { dispatchEvent } from "./api/dispatch";
import AnnounceLayer from "./components/AnnounceLayer";
import ChatInput from "./components/chat/ChatInput";
import InnerStatePanel from "./components/inner/InnerStatePanel";
import SettingsView from "./components/layout/SettingsView";
import ActivityPanel from "./components/panels/ActivityPanel";
import DesiresPanel from "./components/panels/DesiresPanel";
import MemoryPanel from "./components/panels/MemoryPanel";
import MutterCard from "./components/shell/MutterCard";
import RightDock, { type View } from "./components/shell/RightDock";
import ScrollArea from "./components/shell/ScrollArea";
import StatusBar from "./components/shell/StatusBar";
import { usePresence } from "./hooks/usePresence";
import { useSSE } from "./hooks/useSSE";
import { useActivityStore } from "./stores/activityStore";
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

// 装配：顶栏（标题+设置+连接状态）+ 左状态条 + 书卷区（底部工具条替换式切视图：
// 聊天 / 内在状态 / 欲望 / 活动 / 记忆）+ 输入框 + 设置弹层 + 气泡层。
// useSSE 只挂一次；顶栏「设置」开设置弹层。
export default function App() {
  const status = useSSE(dispatchEvent);
  const refreshState = useInnerLifeStore((s) => s.refreshState);
  const refreshActivity = useActivityStore((s) => s.refresh);
  usePresence();
  const [settingsOpen, setSettingsOpen] = useState(false);
  // 书卷区当前视图：null = 聊天主舞台；其余 = 对应面板（RightDock 底部按钮切换）
  const [view, setView] = useState<View>(null);
  const tint = useSettingsStore((s) => s.tint);
  const image = useSettingsStore((s) => s.image);
  const fontScale = useSettingsStore((s) => s.fontScale);

  // SSE 恢复连接后重拉快照（断线期间 emotion_update 可能丢失）
  useEffect(() => {
    if (status === "open") {
      refreshState();
      void refreshActivity();
    }
  }, [status, refreshState, refreshActivity]);

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
          <button
            type="button"
            className="topbar-settings"
            onClick={() => setSettingsOpen(true)}
          >
            设置
          </button>
        </div>
      </header>

      <main className="game-shell" style={shellStyle}>
        <StatusBar />
        <MutterCard />
        <div className="game-main">
          {view === null ? (
            <ScrollArea />
          ) : (
            <section className="side-panel">
              <div className="side-panel__body">
                {view === "inner" && <InnerStatePanel />}
                {view === "desire" && <DesiresPanel />}
                {view === "activity" && <ActivityPanel />}
                {view === "memory" && <MemoryPanel />}
              </div>
            </section>
          )}
        </div>
        <RightDock view={view} onSwitch={setView} />
        <ChatInput />
      </main>

      {settingsOpen && <SettingsView onClose={() => setSettingsOpen(false)} />}
      <AnnounceLayer />
    </div>
  );
}
