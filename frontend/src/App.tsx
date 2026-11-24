import frontendPolicy from "../../nyx/settings/frontend.json";
import BehaviorPanel from "./components/panels/BehaviorPanel";
import { useCharacterStore } from "./stores/characterStore";
import { useConnectionStore } from "./stores/connectionStore";
import { invalidateScope } from "./api/characterScope";
import { pairDevice } from "./api/client";
import CharacterSelect from "./components/layout/CharacterSelect";
import CharacterCreator from "./components/layout/CharacterCreator";
import { useEffect, useState, type CSSProperties } from "react";
import { dispatchEvent, synchronizeAfterReplay } from "./api/dispatch";
import ChatInput from "./components/chat/ChatInput";
import MessageList from "./components/chat/MessageList";
import PetShell from "./components/desktop/PetShell";
import AnnounceLayer from "./components/AnnounceLayer";
import InnerStatePanel from "./components/inner/InnerStatePanel";
import SettingsView from "./components/layout/SettingsView";
import ActivityPanel from "./components/panels/ActivityPanel";
import CreationPanel from "./components/panels/CreationPanel";
import DesiresPanel from "./components/panels/DesiresPanel";
import EvalPanel from "./components/panels/EvalPanel";
import ExpressionHistoryPanel from "./components/panels/ExpressionHistoryPanel";
import MemoryPanel from "./components/panels/MemoryPanel";
import BookshelfView from "./components/reading/BookshelfView";
import ReaderView from "./components/reading/ReaderView";
import RightDock, { type View } from "./components/shell/RightDock";
import { usePresence } from "./hooks/usePresence";
import { useSSE } from "./hooks/useSSE";
import { timePhaseAt } from "./lib/time";
import { useChatStore } from "./stores/chatStore";
import { useReaderStore } from "./stores/readerStore";
import { useSettingsStore } from "./stores/settingsStore";
import { applyDesktopMode, isTauriRuntime, type DesktopMode } from "./lib/desktopWindow";

// 字体大小档 → --text-scale 数值（04 书卷风 §4）。
const FONT_SCALE_VALUE: Record<"small" | "medium" | "large", number> = frontendPolicy.frontend.App.FONT_SCALE_VALUE as Record<"small" | "medium" | "large", number>;

// 装配：左侧图标导航 + 聊天/模块书页 + 设置弹层；useSSE 只挂一次。
export default function App() {
  const required = useConnectionStore((s) => s.required);
  const [token, setToken] = useState("");
  const [code, setCode] = useState("");
  const [deviceName, setDeviceName] = useState(frontendPolicy.copy.App.text);
  const [pairing, setPairing] = useState(false);
  const [error, setError] = useState("");
  if (!required) return <ConnectedApp />;
  return <form className="connection-screen" onSubmit={(event) => {
    event.preventDefault();
    setError("");
    if (pairing) {
      void pairDevice(code.trim(), deviceName.trim()).then(({ access_token }) => {
        useConnectionStore.getState().setToken(access_token);
        setCode("");
      }).catch((reason: unknown) => {
        setError(reason instanceof Error ? reason.message : frontendPolicy.copy.App.text_2);
      });
    } else {
      useConnectionStore.getState().setToken(token);
      setToken("");
    }
  }}>
    <div className="connection-screen__form">
      <h1>{frontendPolicy.copy.App.h1}</h1>
      {pairing ? <>
        <label>{frontendPolicy.copy.App.label}<input inputMode="text" autoComplete="off" value={code} required
          onChange={(event) => setCode(event.target.value)} /></label>
        <label>{frontendPolicy.copy.App.label_2}<input autoComplete="off" value={deviceName} required
          onChange={(event) => setDeviceName(event.target.value)} /></label>
      </> : <label>{frontendPolicy.copy.App.label_3}<input type="password" autoComplete="off" value={token} required
        onChange={(event) => setToken(event.target.value)} /></label>}
      {error && <p role="alert">{error}</p>}
      <button type="submit">{frontendPolicy.copy.App.button}</button>
      <button type="button" onClick={() => { setPairing(!pairing); setError(""); }}>
        {pairing ? frontendPolicy.copy.App.text_3 : frontendPolicy.copy.App.text_4}
      </button>
    </div>
  </form>;
}

function ConnectedApp() {
  const registry = useCharacterStore((s) => s.registry);
  const generation = useCharacterStore((s) => s.generation);
  const switching = useCharacterStore((s) => s.switching);
  const characterError = useCharacterStore((s) => s.error);
  const selected = registry?.characters.find((role) => role.id === registry.current_id);
  const ready = selected?.status === "RUNNING" && !switching;
  const status = useSSE(
    dispatchEvent, ready && selected ? { id: selected.id, generation } : null,
    synchronizeAfterReplay,
  );
  useEffect(() => {
    void useCharacterStore.getState().initialize();
    return () => invalidateScope();
  }, []);
  useEffect(() => {
    const timer = setInterval(() => void useCharacterStore.getState().refresh(), frontendPolicy.timers.character_refresh_ms);
    return () => clearInterval(timer);
  }, []);
  usePresence(status);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [desktopMode, setDesktopMode] = useState<DesktopMode>(() =>
    isTauriRuntime() ? "pet" : "full",
  );
  const [view, setView] = useState<View>("chat");
  const [readerOpen, setReaderOpen] = useState(false);
  const bookId = useReaderStore((s) => s.bookId);
  const messages = useChatStore((s) => s.messages);
  const fontScale = useSettingsStore((s) => s.fontScale);
  const layoutStyle = useSettingsStore((s) => s.layoutStyle ?? "folio");
  const [now, setNow] = useState(() => new Date());

  useEffect(() => {
    let timeout: ReturnType<typeof setTimeout>;
    const update = () => {
      clearTimeout(timeout);
      const current = new Date();
      setNow(current);
      const delay = 60_000 - (current.getSeconds() * 1000 + current.getMilliseconds());
      timeout = setTimeout(update, delay);
    };
    const onVisible = () => {
      if (document.visibilityState === "visible") update();
    };
    update();
    window.addEventListener("focus", update);
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      clearTimeout(timeout);
      window.removeEventListener("focus", update);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, []);

  const shellStyle = {
    "--text-scale": FONT_SCALE_VALUE[fontScale],
  } as CSSProperties;
  const timePhase = timePhaseAt(now);

  useEffect(() => {
    void applyDesktopMode(desktopMode);
  }, [desktopMode]);

  const enterFullDesktop = () => {
    setSettingsOpen(false);
    setDesktopMode("full");
  };

  return (
    <div className={`app app--${desktopMode} app--${layoutStyle}`} data-time-phase={timePhase}>
      <div className="app-bg" aria-hidden="true" />
      <div className="app-time-overlay" aria-hidden="true" />
      {desktopMode === "full" && (
        <>
          {ready && <main
            key={generation}
            className={`game-shell game-shell--${view === "chat" ? "chat" : "page"}${readerOpen ? " game-shell--reader-open" : ""}`}
            style={shellStyle}
          >
            <aside className="app-nav" aria-label={frontendPolicy.copy.App.aria_label}>
              <RightDock
                view={view}
                onSwitch={(nextView) => {
                  setView(nextView);
                  setReaderOpen(false);
                }}
                onOpenSettings={() => {
                  setView("settings");
                  setReaderOpen(false);
                }}
                onReturnToPet={isTauriRuntime() ? () => {
                  setSettingsOpen(false);
                  setDesktopMode("pet");
                } : undefined}
              />
            </aside>
            {view === "chat" ? (
              <>
                <div className="conversation-heading">
                    <button
                      type="button"
                      className="reader-toggle"
                      aria-expanded={readerOpen}
                      onClick={() => setReaderOpen((open) => !open)}
                    >
                      开始阅读
                    </button>
                </div>
                <section className="conversation-column" aria-label={frontendPolicy.copy.App.aria_label_2}>
                  <MessageList messages={messages} now={now} />
                  <ChatInput />
                </section>
                <section className="reader-drawer" aria-label={frontendPolicy.copy.App.aria_label_3}>
                  <div className="reader-drawer__body">
                    {readerOpen && <span className="reader-opening-turn" aria-hidden="true" />}
                    {readerOpen && (bookId === null ? <BookshelfView /> : <ReaderView />)}
                  </div>
                </section>
              </>
            ) : (
              <section className="page-content" aria-label={frontendPolicy.copy.App.aria_label_4}>
                {view === "inner" && <InnerStatePanel />}
                {view === "desire" && <DesiresPanel />}
                {view === "activity" && <ActivityPanel />}
                {(view === "task-compose" || view === "tasks") && <ActivityPanel view="tasks" />}
                {view === "creation" && <CreationPanel />}
                {view === "memory" && <MemoryPanel />}
                {view === "eval" && <EvalPanel />}
                {view === "behavior" && <BehaviorPanel />}
                {view === "history" && <ExpressionHistoryPanel />}
                {view === "settings" && <SettingsView onCreateCharacter={() => setView("character-create")} />}
                {view === "character-create" && <CharacterCreator onBack={() => setView("settings")} />}
              </section>
            )}
          </main>}
          {ready && <div className="full-announcements"><AnnounceLayer /></div>}
        </>
      )}

      {!ready && <div role="status">
        <CharacterSelect />
        <p>{characterError ?? selected?.error ?? (switching ? frontendPolicy.copy.App.text_5 : frontendPolicy.copy.App.text_6)}</p>
        <button onClick={() => setSettingsOpen(true)}>{frontendPolicy.copy.App.button_2}</button>
      </div>}
      {settingsOpen && <SettingsView onClose={() => setSettingsOpen(false)} />}
      {ready && desktopMode === "pet" && (
        <PetShell key={generation}
          night={timePhase === "night"}
          onExpand={enterFullDesktop}
          onOpenSettings={() => setSettingsOpen(true)}
        />
      )}
    </div>
  );
}
