import { useEffect, useState, type CSSProperties } from "react";
import { dispatchEvent } from "./api/dispatch";
import AnnounceLayer from "./components/AnnounceLayer";
import StatusBar from "./components/StatusBar";
import ChatPanel from "./components/chat/ChatPanel";
import EmotionSprite from "./components/inner/EmotionSprite";
import InnerWorld from "./components/layout/InnerWorld";
import SidePanel from "./components/layout/SidePanel";
import Sakura from "./components/scene/Sakura";
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

// App 组合装配（01-sse §6 + 视觉改造布局 §1）：全屏三层——背景柔光 + 樱花 →
// 左侧半身像立绘（常驻）+ 右侧「对话框 / 设置」双模式切换（对话框头部「设置」按钮进入，设置面板「返回对话」退出）。
// useSSE 只挂一次，子组件读 store；背景色调/背景图由 settingsStore 驱动 .app-bg 内联样式。
export default function App() {
  const status = useSSE(dispatchEvent);
  const refreshState = useInnerLifeStore((s) => s.refreshState);
  const refreshActivity = useActivityStore((s) => s.refresh);
  usePresence();
  const [view, setView] = useState<"chat" | "settings">("chat");
  const [innerOpen, setInnerOpen] = useState(false);
  const tint = useSettingsStore((s) => s.tint);
  const image = useSettingsStore((s) => s.image);

  // SSE 恢复连接后重拉快照（断线期间 emotion_update / activity_* 可能丢失）。
  // activity 也在此重拉，保证底部状态条开机即显示当前活动（不再依赖打开「活动」面板）。
  useEffect(() => {
    if (status === "open") {
      refreshState();
      void refreshActivity();
    }
  }, [status, refreshState, refreshActivity]);

  // 背景：有图以图铺底（cover）；无图且有色调用纯色替默认粉渐变。图 + 色并存时叠一层半透明滤镜。
  const bgStyle: CSSProperties = {};
  if (image !== null) {
    bgStyle.backgroundImage = `url(${image})`;
    bgStyle.backgroundSize = "cover";
    bgStyle.backgroundPosition = "center";
  } else if (tint !== null) {
    bgStyle.background = tint;
  }

  return (
    <div className="app">
      <div className="app-bg" aria-hidden="true" style={bgStyle} />
      {tint !== null && image !== null && (
        <div className="app-bg-tint" aria-hidden="true" style={{ backgroundColor: tint }} />
      )}
      <Sakura />

      <header className="app-topbar">
        <span className="scene-title">✦ Nyx ✦</span>
        <div className="topbar-right">
          <span className="connection-state">{CONNECTION_LABEL[status]}</span>
        </div>
      </header>

      <main className="app-stage">
        <EmotionSprite size="portrait" />
        {view === "chat" ? (
          <ChatPanel
            onOpenSettings={() => setView("settings")}
            onToggleInner={() => setInnerOpen((v) => !v)}
          />
        ) : (
          <SidePanel onBack={() => setView("chat")} />
        )}
      </main>
      <InnerWorld open={innerOpen} onClose={() => setInnerOpen(false)} />
      <StatusBar />
      <AnnounceLayer />
    </div>
  );
}
