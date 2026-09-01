import { useEffect, useRef } from "react";
import { useReaderStore } from "../../stores/readerStore";
import ReaderSidebar from "./ReaderSidebar";

// 阅读页：左正文 + 右 Nyx 侧栏（06 §1 组件树）。
// 整屏翻：正文是滚动容器，「下一页/上一页」滚一整屏；onScroll 把页顶段同步回
// userPosition（当前段高亮 + Nyx 位置标记 + 计数都跟着走）。
export default function ReaderView() {
  const bookId = useReaderStore((s) => s.bookId);
  const books = useReaderStore((s) => s.books);
  const totalParagraphs = useReaderStore((s) => s.totalParagraphs);
  const paragraphs = useReaderStore((s) => s.paragraphs);
  const windowFrom = useReaderStore((s) => s.windowFrom);
  const userPosition = useReaderStore((s) => s.userPosition);
  const nyxPosition = useReaderStore((s) => s.nyxPosition);
  const readCount = useReaderStore((s) => s.readCount);
  const syncPosition = useReaderStore((s) => s.syncPosition);
  const closeBook = useReaderStore((s) => s.closeBook);
  const reread = useReaderStore((s) => s.reread);

  const containerRef = useRef<HTMLDivElement | null>(null);
  const paraRefs = useRef<Map<number, HTMLParagraphElement>>(new Map());
  const rafRef = useRef<number | null>(null);

  // 窗口重拉后回页顶（from=userPosition，scrollTop=0 即当前段在页顶）。
  useEffect(() => {
    if (containerRef.current) containerRef.current.scrollTop = 0;
  }, [windowFrom]);

  // 滚到哪、位置就同步到哪（滚轮 + 按钮同一条路）；rAF 节流 + 同段 no-op 去重。
  const handleScroll = () => {
    if (rafRef.current !== null) return;
    rafRef.current = requestAnimationFrame(() => {
      rafRef.current = null;
      const el = containerRef.current;
      if (!el) return;
      const top = el.scrollTop + 8;
      let idx = 0;
      for (const [index, node] of paraRefs.current) {
        if (node.offsetTop <= top && index > idx) idx = index;
      }
      if (idx > 0) void syncPosition(idx);
    });
  };

  // 整屏翻：滚一整屏（smooth）；位置由 onScroll 回填。
  const page = (dir: 1 | -1) => {
    const el = containerRef.current;
    if (el) el.scrollBy({ top: dir * el.clientHeight, behavior: "smooth" });
  };

  const title = books.find((b) => b.id === bookId)?.title ?? "阅读中";

  return (
    <div className="reader">
      <header className="reader__header">
        <button type="button" className="reading-btn" onClick={closeBook}>
          返回书架
        </button>
        <span className="reader__title">{title}</span>
        <span className="reader__pos">
          {userPosition} / {totalParagraphs}
        </span>
      </header>
      <div className="reader__body">
        <div className="reader-text" ref={containerRef} onScroll={handleScroll}>
          {paragraphs.map((p) => {
            const cls = ["reader-text__para"];
            if (p.is_chapter_start) cls.push("reader-text__para--chapter");
            if (p.index === userPosition) cls.push("reader-text__para--current");
            if (p.index === nyxPosition) cls.push("reader-text__para--nyx");
            return (
              <p
                key={p.id}
                className={cls.join(" ")}
                ref={(el) => {
                  if (el) paraRefs.current.set(p.index, el);
                  else paraRefs.current.delete(p.index);
                }}
              >
                {p.text}
              </p>
            );
          })}
        </div>
        <ReaderSidebar />
      </div>
      <footer className="reader__footer">
        <button
          type="button"
          className="reading-btn"
          onClick={() => page(-1)}
          disabled={userPosition <= 1}
        >
          上一页
        </button>
        <button
          type="button"
          className="reading-btn"
          onClick={() => page(1)}
          disabled={userPosition >= totalParagraphs}
        >
          下一页
        </button>
        {readCount >= 1 && (
          <button type="button" className="reading-btn" onClick={() => void reread()}>
            重读
          </button>
        )}
      </footer>
    </div>
  );
}
