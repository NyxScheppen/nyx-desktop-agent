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
  const [text, setText] = useState("");
  const isReplying = useChatStore((s) => s.isReplying);
  const sendMessage = useChatStore((s) => s.sendMessage);

  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (isReplying) return;
    const value = text.trim();
    if (value === "") return;
    void sendMessage(value).then((sent) => {
      if (sent) setText("");
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
      <button type="submit" disabled={isReplying}>发送</button>
    </form>
  );
}

function PanelBack({ onBack }: { onBack: () => void }) {
  return (
    <button type="button" className="pet-panel__back" aria-label="返回上一级" onClick={onBack}>
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
    <section className="pet-panel pet-panel--chat" aria-label="聊天">
      <PanelBack onBack={onBack} />
      <div className="pet-dialogue">
        <span className="pet-dialogue__name">Nyx</span>
        {lastNyx?.content ?? "今天过得怎么样？"}
      </div>
      <FloatingInput placeholder="输入一句话……" />
    </section>
  );
}

function BooksPanel({ books, onBack, onSelect }: {
  books: BookListItem[];
  onBack: () => void;
  onSelect: (bookId: string) => void;
}) {
  return (
    <section className="pet-panel pet-panel--books" aria-label="选择书目">
      <PanelBack onBack={onBack} />
      <PanelCard>
        <h2 className="pet-panel__title">选择书目</h2>
        <p className="pet-panel__text">选择一本书，Nyx 会陪你一起读。</p>
        {books.length === 0 ? (
          <p className="pet-panel__muted">书架空空如也，请在完整桌面端导入 EPUB。</p>
        ) : (
          <div className="pet-book-list">
            {books.map((book) => (
              <button key={book.id} type="button" onClick={() => onSelect(book.id)}>
                <span>{book.title}</span>
                <small>{book.user_position > 0 ? `读到第 ${book.user_position} 段` : "尚未开始"}</small>
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
    <section className="pet-panel pet-panel--reading" aria-label="陪伴读书">
      <PanelBack onBack={onBack} />
      {latestAnnouncement !== undefined && (
        <div className="pet-reading-bubble" aria-live="polite">{latestAnnouncement.text}</div>
      )}
      <div className="pet-reading-card">
        <h2 className="pet-panel__title">陪读 · {book?.title ?? "阅读中"}</h2>
        <div className="pet-reading-content">
          {paragraph?.text ?? "正在读取这一页……"}
        </div>
        <div className="pet-reading-progress">
          <span>你读到：第 {userPosition} 段</span>
          <span>Nyx 读到：第 {nyxPosition} 段</span>
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
      <FloatingInput placeholder="聊聊这一段……" />
    </section>
  );
}

function InnerPanel({ onBack }: { onBack: () => void }) {
  const current = useInnerLifeStore((s) => s.current);
  const activity = useActivityStore((s) => s.data);
  const mood = current === null ? "等待连接" : EMOTION_LABELS[current.emotion];
  const energy = current === null ? "—" : `${Math.round(current.energy)}%`;
  const status = activityStatusText(activity?.current ?? null);

  return (
    <section className="pet-panel pet-panel--inner" aria-label="内心状态">
      <PanelBack onBack={onBack} />
      <PanelCard>
        <h2 className="pet-panel__title">内心状态</h2>
        <div className="pet-inner-state">
          <div><span>心情</span><strong>{mood}</strong></div>
          <div><span>精力</span><strong>{energy}</strong></div>
          <div><span>当前活动</span><strong>{status}</strong></div>
          {current !== null && (
            <div>
              <span>感受</span>
              <strong>{current.valence.toFixed(2)} / {current.arousal.toFixed(2)}</strong>
            </div>
          )}
        </div>
      </PanelCard>
    </section>
  );
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
    await openBook(id);
    setPanel("reading");
  };
  const mood = current === null ? "未知" : EMOTION_LABELS[current.emotion];
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
        className="pet-interaction-layer"
        onPointerDown={(event) => event.stopPropagation()}
        onClick={(event) => event.stopPropagation()}
        onDoubleClick={(event) => event.stopPropagation()}
      >
        <div className="pet-status-bubble" role="status" aria-label="当前状态" aria-live="polite">
          {mood} · 精力 {energy} · {status}
        </div>

        {panel === "fan" && (
          <nav className="pet-fan-menu" aria-label="Nyx 功能菜单">
            <button type="button" className="pet-fan-menu__item pet-fan-menu__item--chat" onClick={() => setPanel("chat")}>聊天</button>
            <button type="button" className="pet-fan-menu__item pet-fan-menu__item--reading" onClick={() => setPanel("books")}>读书</button>
            <button type="button" className="pet-fan-menu__item pet-fan-menu__item--inner" onClick={() => setPanel("inner")}>内心</button>
            <button type="button" className="pet-fan-menu__item pet-fan-menu__item--settings" onClick={() => onOpenSettings?.()}>设置</button>
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
