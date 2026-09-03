import { useLayoutEffect, useRef, useState } from "react";
import { GAP_PX, paginate, useReaderStore } from "../../stores/readerStore";
import { useSettingsStore } from "../../stores/settingsStore";
import NotePanel from "./NotePanel";

// 阅读页（08 §5 真分页）：正文 overflow:hidden 整页切换，取消滚动/滚轮。
// 页序由纯函数 paginate 从段落实测高度算出；pageIndex 从 userPosition（读到第 xx 段）反推，
// 翻页一段一段来：syncPosition(userPosition ± 1) 移动光标，复用既有「前翻逐段补发冲动 + putProgress + 窗口重拉 + 追赶」管线。
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
  const fontScale = useSettingsStore((s) => s.fontScale); // 字号变化 → 段高变 → 重测重分页

  const viewportRef = useRef<HTMLDivElement | null>(null);
  const paraRefs = useRef<Map<number, HTMLParagraphElement>>(new Map());
  const [viewportHeight, setViewportHeight] = useState(0);
  const [pages, setPages] = useState<number[][]>([]);
  const [pageIndex, setPageIndex] = useState(0);
  const [noteOpen, setNoteOpen] = useState(false);

  // measureHeight(index)：第 index 段（全局 1-based）渲染高度 + 段间距 GAP_PX。
  // 读 ref（稳定），不进 effect deps（加进去反而每次 render 重跑）。
  const measureHeight = (index: number) =>
    (paraRefs.current.get(index)?.offsetHeight ?? 0) + GAP_PX;

  // viewportHeight = .reader-text 的 clientHeight，由 ResizeObserver 维护（08 §5.2）。
  useLayoutEffect(() => {
    const el = viewportRef.current;
    if (!el) return;
    const update = () => setViewportHeight(el.clientHeight);
    update();
    const ro = new ResizeObserver(update);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  // 重测重分页：paragraphs / fontScale / viewportHeight / windowFrom 任一变化（08 §5.2）。
  useLayoutEffect(() => {
    setPages(paginate(paragraphs, measureHeight, viewportHeight));
  }, [paragraphs, fontScale, viewportHeight, windowFrom]);

  // pageIndex 从 userPosition（页首段）反推（08 §5.3）：找不到归 0，再 clamp。
  useLayoutEffect(() => {
    if (pages.length === 0) return;
    const found = pages.findIndex((p) => p.includes(userPosition));
    setPageIndex(Math.max(0, Math.min(found < 0 ? 0 : found, pages.length - 1)));
  }, [pages, userPosition]);

  // 一段一段翻（替代 08 §5.4 整页翻页）：上一页/下一页各移动 userPosition ±1，
  // 光标（--current 高亮 =「你读到第 xx 段」）跟着走；pageIndex 由 userPosition 反推，
  // 光标越页界时整页自动切换，窗口越界由 syncPosition 的 needsWindowRefresh 重拉（§5.5 既有）。
  const goPage = (dir: 1 | -1) => {
    void syncPosition(userPosition + dir);
  };

  const title = books.find((b) => b.id === bookId)?.title ?? "阅读中";

  // translateY：当前页前所有段的累计高度（整页视觉切换，08 §5.4）。
  let offset = 0;
  for (let i = 0; i < pageIndex; i++) {
    for (const idx of pages[i]) offset += measureHeight(idx);
  }

  return (
    <div className="reader">
      <header className="reader__header">
        <button type="button" className="reading-btn" onClick={closeBook}>
          返回书架
        </button>
        <span className="reader__title">{title}</span>
        <span className="reader__pos">
          她读到第 {nyxPosition} 段 · 你读到第 {userPosition} / {totalParagraphs} 段
        </span>
      </header>
      <div className="reader__body">
        <div className="reader-text" ref={viewportRef}>
          <div
            className="reader-text__pages"
            style={{ transform: `translateY(-${offset}px)` }}
          >
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
        </div>
      </div>
      <footer className="reader__footer">
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
        {readCount >= 1 && (
          <button type="button" className="reading-btn" onClick={() => void reread()}>
            重读
          </button>
        )}
        <button type="button" className="reading-btn" onClick={() => setNoteOpen(true)}>
          笔记
        </button>
      </footer>
      {noteOpen && <NotePanel onClose={() => setNoteOpen(false)} />}
    </div>
  );
}
