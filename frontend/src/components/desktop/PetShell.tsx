import { formatPolicyText } from "../../lib/policy";
import frontendPolicy from "../../../../nyx/settings/frontend.json";
import CharacterSelect from "../layout/CharacterSelect";
import { captureScope, isScopeCurrent } from "../../api/characterScope";
import { useCharacterStore } from "../../stores/characterStore";
import { useEffect, useState, type FormEvent, type ReactNode } from "react";
import { activityStatusText } from "../../lib/activityResult";
import { EMOTION_LABELS } from "../../lib/labels";
import { useAnnounceStore } from "../../stores/announceStore";
import { useActivityStore } from "../../stores/activityStore";
import { useChatStore } from "../../stores/chatStore";
import { useInnerLifeStore } from "../../stores/innerLifeStore";
import { useReaderStore } from "../../stores/readerStore";
import type { BookListItem } from "../../types/api";
import Avatar from "../inner/Avatar";

type PetPanel = "fan" | "chat" | "books" | "reading" | "inner";

type PetShellProps = {
  night: boolean;
  onExpand: () => void;
  onOpenSettings?: () => void;
};

function FloatingInput({ placeholder }: { placeholder: string }) {
  const text = useCharacterStore((s) => s.drafts[s.registry?.current_id ?? "nyx"] ?? "");
  const setText = useCharacterStore((s) => s.setDraft);
  const isReplying = useChatStore((s) => s.isReplying);
  const sendMessage = useChatStore((s) => s.sendMessage);

  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (isReplying) return;
    const value = text.trim();
    if (value === "") return;
    const scope = captureScope();
    void sendMessage(value).then((sent) => {
      if (sent && isScopeCurrent(scope) && useCharacterStore.getState().drafts[scope?.id ?? "nyx"] === value) setText("");
    });
  };

  return (
    <form className="pet-chat-input" onSubmit={submit}>
      <input
        value={text}
        placeholder={placeholder}
        onChange={(event) => setText(event.target.value)}
        aria-label={placeholder}
      />
      <button type="submit" disabled={isReplying}>{frontendPolicy.copy.PetShell.button}</button>
    </form>
  );
}

function PanelBack({ onBack }: { onBack: () => void }) {
  return (
    <button type="button" className="pet-panel__back" aria-label={frontendPolicy.copy.PetShell.aria_label} onClick={onBack}>
      →
    </button>
  );
}

function PanelCard({ children }: { children: ReactNode }) {
  return <div className="pet-panel__card">{children}</div>;
}

function ChatPanel({ onBack }: { onBack: () => void }) {
  const messages = useChatStore((s) => s.messages);
  const lastNyx = [...messages].reverse().find((message) => message.role === "nyx");
  return (
    <section className="pet-panel pet-panel--chat" aria-label={frontendPolicy.copy.PetShell.aria_label_2}>
      <PanelBack onBack={onBack} />
      <div className="pet-dialogue">
        <span className="pet-dialogue__name"><CurrentName /></span>
        {lastNyx?.content ?? frontendPolicy.copy.PetShell.text}
      </div>
      <FloatingInput placeholder={frontendPolicy.copy.PetShell.placeholder} />
    </section>
  );
}

function BooksPanel({ books, onBack, onSelect }: {
  books: BookListItem[];
  onBack: () => void;
  onSelect: (bookId: string) => void;
}) {
  return (
    <section className="pet-panel pet-panel--books" aria-label={frontendPolicy.copy.PetShell.aria_label_3}>
      <PanelBack onBack={onBack} />
      <PanelCard>
        <h2 className="pet-panel__title">{frontendPolicy.copy.PetShell.aria_label_3}</h2>
        <p className="pet-panel__text">{frontendPolicy.copy.PetShell.p}<CurrentName />{frontendPolicy.copy.PetShell.p_2}</p>
        {books.length === 0 ? (
          <p className="pet-panel__muted">{frontendPolicy.copy.PetShell.p_3}</p>
        ) : (
          <div className="pet-book-list">
            {books.map((book) => (
              <button key={book.id} type="button" onClick={() => onSelect(book.id)}>
                <span>{book.title}</span>
                <small>{book.user_position > 0 ? formatPolicyText(frontendPolicy.templates.PetShell.text, { p0: book.user_position }) : frontendPolicy.copy.PetShell.text_2}</small>
              </button>
            ))}
          </div>
        )}
      </PanelCard>
    </section>
  );
}

function ReadingPanel({ onBack }: { onBack: () => void }) {
  const book = useReaderStore((s) => s.books.find((item) => item.id === s.bookId));
  const paragraphs = useReaderStore((s) => s.paragraphs);
  const totalParagraphs = useReaderStore((s) => s.totalParagraphs);
  const userPosition = useReaderStore((s) => s.userPosition);
  const nyxPosition = useReaderStore((s) => s.nyxPosition);
  const syncPosition = useReaderStore((s) => s.syncPosition);
  const announcements = useAnnounceStore((s) => s.items);
  const paragraph = paragraphs.find((item) => item.index === userPosition) ?? paragraphs[0];
  const latestAnnouncement = announcements[announcements.length - 1];

  const goPage = (direction: -1 | 1) => {
    void syncPosition(userPosition + direction);
  };

  return (
    <section className="pet-panel pet-panel--reading" aria-label={frontendPolicy.copy.PetShell.aria_label_4}>
      <PanelBack onBack={onBack} />
      {latestAnnouncement !== undefined && (
        <div className="pet-reading-bubble" aria-live="polite">{latestAnnouncement.text}</div>
      )}
      <div className="pet-reading-card">
        <h2 className="pet-panel__title">{frontendPolicy.copy.PetShell.h2}{book?.title ?? frontendPolicy.copy.PetShell.text_3}</h2>
        <div className="pet-reading-content" role="region" aria-label={frontendPolicy.copy.PetShell.aria_label_5} tabIndex={0}>
          {paragraph?.text ?? frontendPolicy.copy.PetShell.text_4}
        </div>
        <div className="pet-reading-progress">
          <span>{frontendPolicy.copy.PetShell.span}{userPosition}{frontendPolicy.copy.PetShell.span_2}</span>
          <span><CurrentName />{frontendPolicy.copy.PetShell.span_3}{nyxPosition}{frontendPolicy.copy.PetShell.span_2}</span>
        </div>
        <div className="pet-reading-actions">
          <button
            type="button"
            className="reading-btn"
            onClick={() => goPage(-1)}
            disabled={userPosition <= 1}
          >
            上一页
          </button>
          <button
            type="button"
            className="reading-btn"
            onClick={() => goPage(1)}
            disabled={userPosition >= totalParagraphs}
          >
            下一页
          </button>
        </div>
      </div>
      <FloatingInput placeholder={frontendPolicy.copy.PetShell.placeholder_2} />
    </section>
  );
}

function InnerPanel({ onBack }: { onBack: () => void }) {
  const current = useInnerLifeStore((s) => s.current);
  const activity = useActivityStore((s) => s.data);
  const mood = current === null ? frontendPolicy.copy.PetShell.text_5 : EMOTION_LABELS[current.emotion];
  const energy = current === null ? "—" : `${Math.round(current.energy)}%`;
  const status = activityStatusText(activity?.current ?? null);

  return (
    <section className="pet-panel pet-panel--inner" aria-label={frontendPolicy.copy.PetShell.aria_label_6}>
      <PanelBack onBack={onBack} />
      <PanelCard>
        <h2 className="pet-panel__title">{frontendPolicy.copy.PetShell.aria_label_6}</h2>
        <div className="pet-inner-state">
          <div><span>{frontendPolicy.copy.PetShell.span_4}</span><strong>{mood}</strong></div>
          <div><span>{frontendPolicy.copy.PetShell.span_5}</span><strong>{energy}</strong></div>
          <div><span>{frontendPolicy.copy.PetShell.span_6}</span><strong>{status}</strong></div>
          {current !== null && (
            <div>
              <span>{frontendPolicy.copy.PetShell.span_7}</span>
              <strong>{current.valence.toFixed(2)} / {current.arousal.toFixed(2)}</strong>
            </div>
          )}
        </div>
      </PanelCard>
    </section>
  );
}

function CurrentName() {
  return useCharacterStore((s) => s.registry?.characters.find((r) => r.id === s.registry?.current_id)?.name ?? "Nyx");
}

export default function PetShell({ night, onExpand, onOpenSettings }: PetShellProps) {
  const [panel, setPanel] = useState<PetPanel | null>(null);
  const books = useReaderStore((s) => s.books);
  const bookId = useReaderStore((s) => s.bookId);
  const loadBooks = useReaderStore((s) => s.loadBooks);
  const openBook = useReaderStore((s) => s.openBook);
  const current = useInnerLifeStore((s) => s.current);
  const activity = useActivityStore((s) => s.data);

  useEffect(() => {
    if (panel === "books" && books.length === 0) void loadBooks();
  }, [books.length, loadBooks, panel]);

  const toggleFan = () => setPanel((currentPanel) => currentPanel === "fan" ? null : "fan");
  const selectBook = async (id: string) => {
    const scope = captureScope();
    await openBook(id);
    if (isScopeCurrent(scope)) setPanel("reading");
  };
  const mood = current === null ? frontendPolicy.copy.PetShell.text_6 : EMOTION_LABELS[current.emotion];
  const status = activityStatusText(activity?.current ?? null);
  const energy = current === null ? "—" : `${Math.round(current.energy)}%`;

  return (
    <Avatar
      night={night}
      showAnnouncements={panel !== "reading"}
      useNativeWindowDrag
      onActivate={toggleFan}
      onDoubleClick={onExpand}
    >
      <div
        className={
          panel !== null && panel !== "fan"
            ? "pet-interaction-layer pet-interaction-layer--panel-open"
            : "pet-interaction-layer"
        }
        onPointerDown={(event) => event.stopPropagation()}
        onClick={(event) => event.stopPropagation()}
        onDoubleClick={(event) => event.stopPropagation()}
      >
        <div className="pet-status-bubble" role="status" aria-label={frontendPolicy.copy.PetShell.aria_label_7} aria-live="polite">
          {mood}{frontendPolicy.copy.PetShell.div}{energy} · {status}
        </div>

        {panel === "fan" && (
          <nav className="pet-fan-menu" aria-label={frontendPolicy.copy.PetShell.aria_label_8}>
            <CharacterSelect />
            <button type="button" className="pet-fan-menu__item pet-fan-menu__item--chat" onClick={() => setPanel("chat")}>{frontendPolicy.copy.PetShell.aria_label_2}</button>
            <button type="button" className="pet-fan-menu__item pet-fan-menu__item--reading" onClick={() => setPanel("books")}>{frontendPolicy.copy.PetShell.button_2}</button>
            <button type="button" className="pet-fan-menu__item pet-fan-menu__item--inner" onClick={() => setPanel("inner")}>{frontendPolicy.copy.PetShell.button_3}</button>
            <button type="button" className="pet-fan-menu__item pet-fan-menu__item--settings" onClick={() => onOpenSettings?.()}>{frontendPolicy.copy.PetShell.button_4}</button>
          </nav>
        )}

        {panel === "chat" && <ChatPanel onBack={() => setPanel("fan")} />}
        {panel === "books" && <BooksPanel books={books} onBack={() => setPanel("fan")} onSelect={selectBook} />}
        {panel === "reading" && bookId !== null && <ReadingPanel onBack={() => setPanel("books")} />}
        {panel === "inner" && <InnerPanel onBack={() => setPanel("fan")} />}
      </div>
    </Avatar>
  );
}
