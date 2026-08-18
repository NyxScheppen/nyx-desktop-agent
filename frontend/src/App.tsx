import { useEffect } from "react";
import { dispatchEvent } from "./api/dispatch";
import ChatPanel from "./components/chat/ChatPanel";
import InnerStatePanel from "./components/inner/InnerStatePanel";
import Panel from "./components/layout/Panel";
import { usePresence } from "./hooks/usePresence";
import { useSSE } from "./hooks/useSSE";
import { useInnerLifeStore } from "./stores/innerLifeStore";
import type { ConnectionState } from "./types/api";

const CONNECTION_LABEL: Record<ConnectionState, string> = {
  connecting: "重连中",
  open: "已连接",
  closed: "已断开",
};

// App 组合装配（01-sse §6）：useSSE 只挂一次，子面板读 store
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
      <header className="app-header">
        <h1>Nyx</h1>
        <span className="connection-state">{CONNECTION_LABEL[status]}</span>
      </header>
      <main className="app-main">
        <ChatPanel />
        <InnerStatePanel />
        <Panel title="欲望" placeholder />
        <Panel title="活动" placeholder />
        <Panel title="记忆" placeholder />
        <Panel title="Eval" placeholder />
        <Panel title="溯源" placeholder />
      </main>
    </div>
  );
}
