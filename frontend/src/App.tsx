import ChatPanel from "./components/chat/ChatPanel";
import InnerStatePanel from "./components/inner/InnerStatePanel";
import Panel from "./components/layout/Panel";

// AppLayout 面板骨架：核心先行 2 真面板 + 5 占位面板（frontend/README §5）
export default function App() {
  return (
    <div className="app">
      <header className="app-header">
        <h1>Nyx</h1>
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
