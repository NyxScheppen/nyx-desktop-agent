import { useState } from "react";
import {
  nyxStatusOf,
  useReaderStore,
  type NyxStatus,
  type ReadingBubbleKind,
} from "../../stores/readerStore";
import NotePanel from "./NotePanel";

const STATUS_LABEL: Record<NyxStatus, string> = {
  idle: "未在读书",
  reading: "正在追赶…",
  waiting: "等你翻页",
};

const BUBBLE_LABEL: Record<ReadingBubbleKind, string> = {
  mutter: "碎碎念",
  question: "提问",
  association: "联想",
};

// 右侧 Nyx 侧栏：追赶状态 + 位置 + 进度条 + 冲动气泡流 + 笔记入口（07 §2）。
export default function ReaderSidebar() {
  const bookId = useReaderStore((s) => s.bookId);
  const nyxPosition = useReaderStore((s) => s.nyxPosition);
  const userPosition = useReaderStore((s) => s.userPosition);
  const impulseBubbles = useReaderStore((s) => s.impulseBubbles);
  const [noteOpen, setNoteOpen] = useState(false);

  const status = nyxStatusOf(bookId, nyxPosition, userPosition);
  const progress =
    userPosition > 0 ? Math.min(100, Math.round((nyxPosition / userPosition) * 100)) : 0;

  return (
    <aside className="reader-sidebar">
      <div className="reader-sidebar__title">尼克斯</div>
      <div className="reader-sidebar__status">{STATUS_LABEL[status]}</div>
      <div className="reader-sidebar__pos">
        她读到第 {nyxPosition} 段
        <br />
        你读到第 {userPosition} 段
      </div>
      <div className="reader-sidebar__progress">
        <div className="reader-sidebar__progress-fill" style={{ width: `${progress}%` }} />
      </div>
      <div className="reader-sidebar__bubbles">
        {impulseBubbles.map((b) => (
          <div key={b.id} className={`reader-bubble reader-bubble--${b.kind}`}>
            <span className="reader-bubble__label">{BUBBLE_LABEL[b.kind]}</span>
            <span className="reader-bubble__text">{b.content}</span>
          </div>
        ))}
      </div>
      <button type="button" className="reading-btn" onClick={() => setNoteOpen(true)}>
        笔记
      </button>
      {noteOpen && <NotePanel onClose={() => setNoteOpen(false)} />}
    </aside>
  );
}
