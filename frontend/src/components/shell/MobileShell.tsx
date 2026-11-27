import { useEffect, useRef, useState, type CSSProperties } from "react";
import frontendPolicy from "../../../../nyx/settings/frontend.json";
import readingPolicy from "../../../../nyx/settings/reading.json";
import { useReaderStore } from "../../stores/readerStore";
import { useCharacterStore } from "../../stores/characterStore";
import ChatInput from "../chat/ChatInput";
import MessageList from "../chat/MessageList";
import BookshelfView from "../reading/BookshelfView";
import ReaderView, { type ReaderPage } from "../reading/ReaderView";
import EmotionSprite from "../inner/EmotionSprite";
import AnnounceLayer from "../AnnounceLayer";
import type { ChatMessage } from "../../stores/chatStore";
import type { View } from "./RightDock";
import "./mobile.css";

const copy = frontendPolicy.copy.Mobile;
const settings = frontendPolicy.copy.RightDock.aria_label;
const library = copy.library;
const entries = frontendPolicy.frontend.RightDock.ENTRIES;

type Route = { view: string; book?: string; page?: ReaderPage };
const readerPages = new Set<string>(["contents", "highlights", "bookmarks", "notes", "new-note", "companion"]);

function readRoute(): Route {
  const [view, encodedBook, page] = window.location.hash.slice(1).split("/");
  if (view === "reader" && encodedBook) {
    try {
      return { view, book: decodeURIComponent(encodedBook), page: readerPages.has(page) ? page as ReaderPage : undefined };
    } catch {
      return { view: "library" };
    }
  }
  return { view: view === "library" || view === "settings" || entries.some((entry) => entry.view === view && view !== "history") ? view : "chat" };
}

export default function MobileShell({ style, messages, now, renderPanel }: {
  style: CSSProperties;
  messages: ChatMessage[];
  now: Date;
  renderPanel: (view: View) => React.ReactNode;
}) {
  const [route, setRoute] = useState(readRoute);
  const drawer = useRef<HTMLDialogElement>(null);
  const role = useCharacterStore((s) => s.registry?.characters.find((item) => item.id === s.registry?.current_id));
  const bookId = useReaderStore((s) => s.bookId);
  const bookError = useReaderStore((s) => s.booksError);
  const books = useReaderStore((s) => s.books);
  const openBook = useReaderStore((s) => s.openBook);
  const lastBook = useRef<string | undefined>(undefined);
  const target = route.book;

  useEffect(() => {
    function changed() {
      drawer.current?.close();
      setRoute(readRoute());
    }
    window.addEventListener("hashchange", changed);
    function back(event: Event) {
      if (drawer.current?.open) drawer.current.close();
      else if ((event as CustomEvent<boolean>).detail) window.history.back();
      else if (window.location.hash) window.location.hash = "chat";
    }
    window.addEventListener("nyx:back", back);
    return () => {
      window.removeEventListener("hashchange", changed);
      window.removeEventListener("nyx:back", back);
    };
  }, []);

  useEffect(() => {
    if (!target) {
      useReaderStore.getState().stopCatchup();
      lastBook.current = undefined;
      return;
    }
    if (lastBook.current !== target) {
      lastBook.current = target;
      void openBook(target);
    }
  }, [openBook, target]);

  useEffect(() => {
    if (!target) return;
    function onVisibility() {
      const reader = useReaderStore.getState();
      if (document.hidden) reader.stopCatchup();
      else if (reader.bookId === target) void reader.refreshProgress();
    }
    document.addEventListener("visibilitychange", onVisibility);
    window.addEventListener("nyx:active", onVisibility);
    return () => {
      document.removeEventListener("visibilitychange", onVisibility);
      window.removeEventListener("nyx:active", onVisibility);
      useReaderStore.getState().stopCatchup();
    };
  }, [target]);

  function go(view: string, book?: string, page?: ReaderPage) {
    drawer.current?.close();
    window.location.hash = [view, book && encodeURIComponent(book), page].filter(Boolean).join("/");
  }

  function open(id: string) {
    go("reader", id);
  }

  const title = route.view === "reader" ? books.find((book) => book.id === target)?.title ?? readingPolicy.copy.ReaderView.text
    : route.view === "chat" ? copy.chat
    : route.view === "library" ? library
    : route.view === "settings" ? settings
    : entries.find((entry) => entry.view === route.view)?.label;
  const sections = [
    { name: copy.daily, views: ["chat", "library", "activity", "memory"] },
    { name: role?.name ?? copy.character, views: ["inner", "desire", "creation"] },
    { name: copy.management, views: ["behavior", "eval", "settings"] },
  ];
  const active = route.view === "reader" ? "library" : route.view;

  return (
    <main className="nyx-mobile" style={style}>
      <header className="nyx-mobile__header">
        <button type="button" aria-label={copy.menu} onClick={() => drawer.current?.showModal()}>☰</button>
        <h1>{title}</h1>
        {route.view === "reader" &&
          <button type="button" onClick={() => go("chat")}>{copy.chat}</button>}
      </header>
      <div className="nyx-mobile__body">
        {route.view === "chat" && <section className="nyx-mobile__chat" aria-label={copy.chat}>
          <MessageList messages={messages} now={now} onOpenBook={open} />
          <ChatInput showReplyHint={false} />
        </section>}
        {route.view === "library" && <div className="nyx-mobile__page"><BookshelfView onOpenBook={open} /></div>}
        {route.view === "reader" && <>
          {bookError && <div className="nyx-mobile__error">
            <p role="alert">{bookError}</p>
            <button type="button" onClick={() => target && void openBook(target)}>{copy.retry}</button>
            <button type="button" onClick={() => go("library")}>{library}</button>
          </div>}
          {!bookError && bookId === target && <ReaderView
            mobilePage={route.page ?? "reader"}
            onMobilePage={(page) => {
              if (page === "reader" && route.page) {
                window.history.replaceState(null, "", "#reader/" + encodeURIComponent(target));
                setRoute(readRoute());
              } else go("reader", target, page === "reader" ? undefined : page);
            }}
            onCloseBook={() => go("library")}
          />}
        </>}
        {!["chat", "library", "reader"].includes(route.view) &&
          <div className="nyx-mobile__page">{renderPanel(route.view as View)}</div>}
      </div>
      <dialog ref={drawer} className="nyx-mobile__drawer" aria-label={copy.menu}
        onClick={(event) => { if (event.target === event.currentTarget) event.currentTarget.close(); }}>
        <div className="nyx-mobile__drawer-content">
          <header>
            <EmotionSprite size="circle" />
            <strong>{role?.name ?? copy.character}</strong>
            <button type="button" aria-label={copy.close_menu} onClick={() => drawer.current?.close()}>×</button>
          </header>
          <nav>
            {sections.map(({ name, views }) => <section key={name}>
              <h2>{name}</h2>
              {views.map((view) => {
                const item = entries.find((entry) => entry.view === view);
                const label = view === "library" ? library : view === "chat" ? copy.chat : view === "settings" ? settings : item?.label;
                const icon = view === "library" ? "▤" : view === "settings" ? "⚙" : item?.icon;
                return <button key={view} type="button" aria-current={active === view ? "page" : undefined} onClick={() => go(view)}>
                  <span aria-hidden="true">{icon}</span>{label}
                </button>;
              })}
            </section>)}
          </nav>
        </div>
      </dialog>
      <aside className="nyx-mobile__announcements"><AnnounceLayer readingBook={target} /></aside>
    </main>
  );
}
