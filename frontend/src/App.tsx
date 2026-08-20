import { useEffect } from "react";
import { dispatchEvent } from "./api/dispatch";
import ChatPanel from "./components/chat/ChatPanel";
import EmotionSprite from "./components/inner/EmotionSprite";
import SideDrawer from "./components/layout/SideDrawer";
import Sakura from "./components/scene/Sakura";
import { usePresence } from "./hooks/usePresence";
import { useSSE } from "./hooks/useSSE";
import { useInnerLifeStore } from "./stores/innerLifeStore";
import type { ConnectionState } from "./types/api";

const CONNECTION_LABEL: Record<ConnectionState, string> = {
  connecting: "重连中",
  open: "已连接",
  closed: "已断开",
};

// App 组合装配（01-sse §6 + 视觉改造布局 §1）：全屏三层——
// 背景柔光 + 樱花 → 左侧半身像立绘 + 右侧微信式聊天窗；侧栏抽屉收非对话面板。
// useSSE 只挂一次，子组件读 store。
export default function App() {
  const status = useSSE(dispatchEvent);
  const refreshState = useInnerLifeStore((s) => s.refreshState);
  usePresence();

  // SSE 恢复连接后重拉快照（断线期间 emotion_update 可能丢失）
  useEffect(() => {
    if (status === "open") refreshState();
  }, [status, refreshState]);

  return (
    <div className="app">
      <div className="app-bg" aria-hidden="true" />
      <Sakura />

      <header className="app-topbar">
        <span className="scene-title">✦ Nyx ✦</span>
        <div className="topbar-right">
          <span className="connection-state">{CONNECTION_LABEL[status]}</span>
          <SideDrawer />
        </div>
      </header>

      <main className="app-stage">
        <EmotionSprite size="portrait" />
        <ChatPanel />
      </main>
    </div>
  );
}
