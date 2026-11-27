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
import MobileShell from "./components/shell/MobileShell";
import { connectAndroid, postToAndroid } from "./lib/mobileBridge";
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
  const mobile = !isTauriRuntime() && window.location.pathname.replace(/\/$/, "") === "/mobile";
  const required = useConnectionStore((s) => s.required);
  const [token, setToken] = useState("");
  const [code, setCode] = useState("");
  const [deviceName, setDeviceName] = useState(frontendPolicy.copy.App.text);
  const [pairing, setPairing] = useState(mobile);
  const [redeeming, setRedeeming] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    if (mobile) return connectAndroid();
  }, [mobile]);
  if (!required) return <ConnectedApp mobile={mobile} />;
  return <form className="connection-screen" onSubmit={(event) => {
    event.preventDefault();
    setError("");
    if (pairing) {
      if (redeeming) return;
      setRedeeming(true);
      void pairDevice(code.trim(), deviceName.trim()).then(({ access_token }) => {
        useConnectionStore.getState().setToken(access_token);
        if (mobile) postToAndroid("paired", { token: access_token });
        setCode("");
      }).catch((reason: unknown) => {
        setError(reason instanceof Error ? reason.message : frontendPolicy.copy.App.text_2);
      }).finally(() => setRedeeming(false));
    } else {
      useConnectionStore.getState().setToken(token);
      setToken("");
    }
  }}>
    <div className="connection-screen__form">
      <h1>{frontendPolicy.copy.App.h1}</h1>
      {pairing ? <>
        <label>{frontendPolicy.copy.App.label}<input inputMode="text" autoComplete="off" autoCapitalize="none"
          autoCorrect="off" spellCheck={false} aria-describedby="nyx-pairing-help" value={code} required
          onChange={(event) => setCode(event.target.value)} /></label>
        <p id="nyx-pairing-help">{frontendPolicy.copy.App.pairing_help}</p>
        <label>{frontendPolicy.copy.App.label_2}<input autoComplete="off" value={deviceName} required
          onChange={(event) => setDeviceName(event.target.value)} /></label>
      </> : <label>{frontendPolicy.copy.App.label_3}<input type="password" autoComplete="off" value={token} required
        onChange={(event) => setToken(event.target.value)} /></label>}
      {error && <p role="alert">{error}</p>}
      <button type="submit" disabled={redeeming}>{frontendPolicy.copy.App.button}</button>
      {!mobile && <button type="button" disabled={redeeming} onClick={() => { setPairing(!pairing); setError(""); }}>
        {pairing ? frontendPolicy.copy.App.text_3 : frontendPolicy.copy.App.text_4}
      </button>}
    </div>
  </form>;
}

function ConnectedApp({ mobile }: { mobile: boolean }) {
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

  function renderPanel(panel: View) {
    if (panel === "inner") return <InnerStatePanel />;
    if (panel === "desire") return <DesiresPanel />;
    if (panel === "activity") return <ActivityPanel />;
    if (panel === "task-compose" || panel === "tasks") return <ActivityPanel view="tasks" />;
    if (panel === "creation") return <CreationPanel />;
    if (panel === "memory") return <MemoryPanel />;
    if (panel === "eval") return <EvalPanel />;
    if (panel === "behavior") return <BehaviorPanel />;
    if (panel === "history") return <ExpressionHistoryPanel />;
    if (panel === "settings") return <SettingsView deviceOnly={mobile} onCreateCharacter={mobile ? undefined : () => setView("character-create")} />;
    if (panel === "character-create") return <CharacterCreator onBack={() => setView("settings")} />;
  }

  return (
    <div className={`app app--${desktopMode} app--${layoutStyle}`} data-time-phase={timePhase}>
      <div className="app-bg" aria-hidden="true" />
      <div className="app-time-overlay" aria-hidden="true" />
      {mobile && ready && <MobileShell key={generation} style={shellStyle} messages={messages} now={now} renderPanel={renderPanel} />}
      {!mobile && desktopMode === "full" && (
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
                  <MessageList messages={messages} now={now} onOpenBook={(id) => {
                    setReaderOpen(true);
                    void useReaderStore.getState().openBook(id);
                  }} />
                  <ChatInput />
                </section>
                <section className="reader-drawer" aria-label={frontendPolicy.copy.App.aria_label_3}>
                  <div className="reader-drawer__body">
                    {readerOpen && (bookId === null ? <BookshelfView /> : <ReaderView />)}
                  </div>
                </section>
              </>
            ) : (
              <section className="page-content" aria-label={frontendPolicy.copy.App.aria_label_4}>
                {renderPanel(view)}
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
      {settingsOpen && <SettingsView deviceOnly={mobile} onClose={() => setSettingsOpen(false)} />}
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
