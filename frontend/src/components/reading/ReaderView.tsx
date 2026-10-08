import {
  createElement,
  Fragment,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import {
  GAP_PX,
  nyxStatusOf,
  paginate,
  useReaderStore,
} from "../../stores/readerStore";
import { useSettingsStore } from "../../stores/settingsStore";
import type {
  Paragraph,
  ParagraphBlock,
  TextMark,
  UserNoteWithAnnotations,
} from "../../types/api";
import Modal from "../layout/Modal";
import NotePanel from "./NotePanel";

type SelectionDraft = {
  paragraphId: string;
  text: string;
  start: number;
  end: number;
  left: number;
  top: number;
};

type MarkPanel = "highlights" | "bookmarks" | null;

function selectionRoot(node: Node | null): HTMLElement | null {
  const element =
    node instanceof Element ? node : node?.parentElement ?? null;
  return element?.closest<HTMLElement>("[data-paragraph-id]") ?? null;
}

function selectionOffset(node: Node, offset: number): number | null {
  if (node.nodeType !== Node.TEXT_NODE) return null;
  const owner = node.parentElement?.closest<HTMLElement>("[data-text-start]");
  if (owner === undefined || owner === null) return null;
  const start = Number(owner.dataset.textStart);
  return Number.isFinite(start) ? start + offset : null;
}

function validHighlight(
  note: UserNoteWithAnnotations,
): note is UserNoteWithAnnotations & {
  paragraph_id: string;
  paragraph_index: number;
  selected_text: string;
  selection_start: number;
  selection_end: number;
} {
  return (
    note.paragraph_id !== null &&
    typeof note.paragraph_index === "number" &&
    typeof note.selected_text === "string" &&
    note.selected_text.length > 0 &&
    typeof note.selection_start === "number" &&
    typeof note.selection_end === "number" &&
    note.selection_end > note.selection_start
  );
}

function blockTag(block: ParagraphBlock): keyof HTMLElementTagNameMap {
  if (block.kind === "heading") {
    const level = Math.max(1, Math.min(6, block.level ?? 2));
    return ("h" + level) as keyof HTMLElementTagNameMap;
  }
  if (block.kind === "blockquote") return "blockquote";
  if (block.kind === "pre") return "pre";
  return "p";
}

type RangeDelta = {
  bold: number;
  italic: number;
  highlight: number;
  focused: number;
};

export function renderRange(
  paragraph: Paragraph,
  start: number,
  end: number,
  marks: TextMark[],
  highlights: UserNoteWithAnnotations[],
  focusedHighlightId: string | null,
): ReactNode[] {
  const events = new Map<number, RangeDelta>();
  const eventAt = (position: number): RangeDelta => {
    const existing = events.get(position);
    if (existing !== undefined) return existing;
    const created = { bold: 0, italic: 0, highlight: 0, focused: 0 };
    events.set(position, created);
    return created;
  };
  eventAt(start);
  eventAt(end);
  for (const mark of marks) {
    const rangeStart = Math.max(start, mark.start);
    const rangeEnd = Math.min(end, mark.end);
    if (rangeStart >= rangeEnd) continue;
    const opening = eventAt(rangeStart);
    const closing = eventAt(rangeEnd);
    if (mark.bold) {
      opening.bold += 1;
      closing.bold -= 1;
    }
    if (mark.italic) {
      opening.italic += 1;
      closing.italic -= 1;
    }
  }
  for (const note of highlights) {
    const noteStart = note.selection_start ?? 0;
    const noteEnd = note.selection_end ?? 0;
    const rangeStart = Math.max(start, noteStart);
    const rangeEnd = Math.min(end, noteEnd);
    if (rangeStart >= rangeEnd) continue;
    const opening = eventAt(rangeStart);
    const closing = eventAt(rangeEnd);
    opening.highlight += 1;
    closing.highlight -= 1;
    if (note.id === focusedHighlightId) {
      opening.focused += 1;
      closing.focused -= 1;
    }
  }
  const points = [...events.keys()].sort((a, b) => a - b);
  const nodes: ReactNode[] = [];
  const active = { bold: 0, italic: 0, highlight: 0, focused: 0 };
  for (let index = 0; index < points.length - 1; index += 1) {
    const atomStart = points[index];
    const atomEnd = points[index + 1];
    const delta = events.get(atomStart);
    if (delta !== undefined) {
      active.bold += delta.bold;
      active.italic += delta.italic;
      active.highlight += delta.highlight;
      active.focused += delta.focused;
    }
    if (atomStart === atomEnd) continue;
    const classes = ["reader-run"];
    if (active.bold > 0) classes.push("reader-run--bold");
    if (active.italic > 0) classes.push("reader-run--italic");
    if (active.highlight > 0) classes.push("reader-run--highlight");
    if (active.focused > 0) classes.push("reader-run--focused");
    nodes.push(
      <span
        key={atomStart + ":" + atomEnd}
        className={classes.join(" ")}
        data-text-start={atomStart}
      >
        {paragraph.text.slice(atomStart, atomEnd)}
      </span>,
    );
  }
  return nodes;
}

function ParagraphContent({
  paragraph,
  highlights,
  focusedHighlightId,
}: {
  paragraph: Paragraph;
  highlights: UserNoteWithAnnotations[];
  focusedHighlightId: string | null;
}) {
  const marks = paragraph.marks ?? [];
  const blocks =
    paragraph.blocks !== undefined && paragraph.blocks.length > 0
      ? paragraph.blocks
      : [{
          kind: paragraph.is_chapter_start ? "heading" : "paragraph",
          start: 0,
          end: paragraph.text.length,
          level: paragraph.is_chapter_start ? 2 : null,
        } satisfies ParagraphBlock];

  return (
    <>
      {blocks.map((block, index) => (
        <Fragment key={block.start + ":" + block.end + ":" + block.kind}>
          {createElement(
            blockTag(block),
            {
              className: "reader-block reader-block--" + block.kind,
            },
            renderRange(
              paragraph,
              block.start,
              block.end,
              marks,
              highlights,
              focusedHighlightId,
            ),
          )}
          {index < blocks.length - 1 ? "\n" : null}
        </Fragment>
      ))}
    </>
  );
}

export default function ReaderView() {
  const bookId = useReaderStore((state) => state.bookId);
  const books = useReaderStore((state) => state.books);
  const totalParagraphs = useReaderStore((state) => state.totalParagraphs);
  const paragraphs = useReaderStore((state) => state.paragraphs);
  const windowFrom = useReaderStore((state) => state.windowFrom);
  const userPosition = useReaderStore((state) => state.userPosition);
  const nyxPosition = useReaderStore((state) => state.nyxPosition);
  const readCount = useReaderStore((state) => state.readCount);
  const notes = useReaderStore((state) => state.notes);
  const bookmarks = useReaderStore((state) => state.bookmarks);
  const syncPosition = useReaderStore((state) => state.syncPosition);
  const jumpToPosition = useReaderStore((state) => state.jumpToPosition);
  const closeBook = useReaderStore((state) => state.closeBook);
  const reread = useReaderStore((state) => state.reread);
  const loadNotes = useReaderStore((state) => state.loadNotes);
  const addNote = useReaderStore((state) => state.addNote);
  const deleteNote = useReaderStore((state) => state.deleteNote);
  const loadBookmarks = useReaderStore((state) => state.loadBookmarks);
  const toggleBookmark = useReaderStore((state) => state.toggleBookmark);
  const fontScale = useSettingsStore((state) => state.fontScale);

  const viewportRef = useRef<HTMLDivElement | null>(null);
  const paraRefs = useRef<Map<number, HTMLElement>>(new Map());
  const [viewportHeight, setViewportHeight] = useState(0);
  const [pages, setPages] = useState<number[][]>([]);
  const [pageIndex, setPageIndex] = useState(0);
  const [noteOpen, setNoteOpen] = useState(false);
  const [markPanel, setMarkPanel] = useState<MarkPanel>(null);
  const [query, setQuery] = useState("");
  const [selection, setSelection] = useState<SelectionDraft | null>(null);
  const [focusedHighlightId, setFocusedHighlightId] = useState<string | null>(
    null,
  );

  const measureHeight = (index: number) =>
    (paraRefs.current.get(index)?.offsetHeight ?? 0) + GAP_PX;

  useEffect(() => {
    void loadNotes();
    void loadBookmarks();
  }, [bookId, loadBookmarks, loadNotes]);

  useLayoutEffect(() => {
    const element = viewportRef.current;
    if (element === null) return;
    const update = () => setViewportHeight(element.clientHeight);
    update();
    const observer = new ResizeObserver(update);
    observer.observe(element);
    return () => observer.disconnect();
  }, []);

  useLayoutEffect(() => {
    setPages(paginate(paragraphs, measureHeight, viewportHeight));
  }, [paragraphs, notes, fontScale, viewportHeight, windowFrom]);

  useLayoutEffect(() => {
    if (pages.length === 0) return;
    const found = pages.findIndex((page) => page.includes(userPosition));
    setPageIndex(Math.max(0, Math.min(found < 0 ? 0 : found, pages.length - 1)));
  }, [pages, userPosition]);

  useEffect(() => {
    setSelection(null);
    window.getSelection()?.removeAllRanges();
  }, [pageIndex, windowFrom, fontScale]);

  const highlightsByParagraph = useMemo(() => {
    const result = new Map<string, UserNoteWithAnnotations[]>();
    for (const note of notes) {
      if (!validHighlight(note)) continue;
      const entries = result.get(note.paragraph_id) ?? [];
      entries.push(note);
      result.set(note.paragraph_id, entries);
    }
    return result;
  }, [notes]);

  const searchedHighlights = useMemo(() => {
    const normalized = query.trim().toLocaleLowerCase();
    return notes
      .filter(validHighlight)
      .filter(
        (note) =>
          normalized === "" ||
          note.selected_text.toLocaleLowerCase().includes(normalized),
      );
  }, [notes, query]);

  const handleSelection = () => {
    const browserSelection = window.getSelection();
    if (
      browserSelection === null ||
      browserSelection.isCollapsed ||
      browserSelection.rangeCount === 0
    ) {
      setSelection(null);
      return;
    }
    const anchorRoot = selectionRoot(browserSelection.anchorNode);
    const focusRoot = selectionRoot(browserSelection.focusNode);
    if (
      anchorRoot === null ||
      focusRoot === null ||
      anchorRoot !== focusRoot
    ) {
      setSelection(null);
      return;
    }
    const anchorNode = browserSelection.anchorNode;
    const focusNode = browserSelection.focusNode;
    if (anchorNode === null || focusNode === null) {
      setSelection(null);
      return;
    }
    const anchor = selectionOffset(anchorNode, browserSelection.anchorOffset);
    const focus = selectionOffset(focusNode, browserSelection.focusOffset);
    if (anchor === null || focus === null || anchor === focus) {
      setSelection(null);
      return;
    }
    const start = Math.min(anchor, focus);
    const end = Math.max(anchor, focus);
    const paragraph = paragraphs.find(
      (item) => item.id === anchorRoot.dataset.paragraphId,
    );
    if (paragraph === undefined) {
      setSelection(null);
      return;
    }
    const text = paragraph.text.slice(start, end);
    if (text.trim() === "") {
      setSelection(null);
      return;
    }
    const rect = browserSelection.getRangeAt(0).getBoundingClientRect();
    const viewportRect = viewportRef.current?.getBoundingClientRect();
    setSelection({
      paragraphId: paragraph.id,
      text,
      start,
      end,
      left: viewportRect === undefined ? 24 : rect.left - viewportRect.left,
      top: viewportRect === undefined ? 24 : rect.top - viewportRect.top - 42,
    });
  };

  const saveHighlight = async () => {
    if (selection === null || bookId === null) return;
    const saved = await addNote({
      book_id: bookId,
      paragraph_id: selection.paragraphId,
      content: "",
      selected_text: selection.text,
      selection_start: selection.start,
      selection_end: selection.end,
    });
    if (saved) {
      setSelection(null);
      window.getSelection()?.removeAllRanges();
    }
  };

  const visitHighlight = async (
    note: UserNoteWithAnnotations & { paragraph_index: number },
  ) => {
    setFocusedHighlightId(note.id);
    setMarkPanel(null);
    await jumpToPosition(note.paragraph_index);
  };

  const currentParagraph = paragraphs.find(
    (paragraph) => paragraph.index === userPosition,
  );
  const currentBookmark = bookmarks.find(
    (bookmark) => bookmark.paragraph_id === currentParagraph?.id,
  );
  const book = books.find((item) => item.id === bookId);
  const title = book?.title ?? "阅读中";
  const author = book?.author ?? "";
  const nyxStatus = nyxStatusOf(bookId, nyxPosition, userPosition);
  const nyxStatusText = nyxStatus === "reading" ? "她正在追上你" : "她在等你";

  let offset = 0;
  for (let index = 0; index < pageIndex; index += 1) {
    for (const paragraphIndex of pages[index]) {
      offset += measureHeight(paragraphIndex);
    }
  }

  return (
    <div className="reader">
      <header className="reader__header">
        <button
          type="button"
          className="reader-tool reader-tool--icon"
          aria-label="返回书架"
          title="返回书架"
          onClick={closeBook}
        >
          ←
        </button>
        <div className="reader__identity">
          <strong className="reader__title">{title}</strong>
          {author !== "" && <span className="reader__author">{author}</span>}
        </div>
        <span className="reader__pos">
          {nyxStatusText} · 她读到第 {nyxPosition} 段 · 你读到第 {userPosition} / {totalParagraphs} 段
        </span>
        <button
          type="button"
          className="reader-tool reader-tool--icon"
          aria-label={currentBookmark === undefined ? "添加书签" : "取消书签"}
          title={currentBookmark === undefined ? "添加书签" : "取消书签"}
          disabled={currentParagraph === undefined}
          onClick={() => {
            if (currentParagraph !== undefined) {
              void toggleBookmark(currentParagraph.id);
            }
          }}
        >
          {currentBookmark === undefined ? "☆" : "★"}
        </button>
      </header>
      <div className="reader__body">
        <div
          className="reader-text"
          ref={viewportRef}
          onMouseUp={handleSelection}
        >
          <div
            className="reader-text__pages"
            style={{ transform: "translateY(-" + offset + "px)" }}
          >
            {paragraphs.map((paragraph) => {
              const classes = ["reader-text__para"];
              if (paragraph.is_chapter_start) {
                classes.push("reader-text__para--chapter");
              }
              if (paragraph.index === userPosition) {
                classes.push("reader-text__para--current");
              }
              if (paragraph.index === nyxPosition) {
                classes.push("reader-text__para--nyx");
              }
              return (
                <article
                  key={paragraph.id}
                  className={classes.join(" ")}
                  data-paragraph-id={paragraph.id}
                  ref={(element) => {
                    if (element !== null) {
                      paraRefs.current.set(paragraph.index, element);
                    } else {
                      paraRefs.current.delete(paragraph.index);
                    }
                  }}
                  style={{
                    maxHeight:
                      viewportHeight > 0
                        ? Math.max(1, viewportHeight - GAP_PX) + "px"
                        : undefined,
                  }}
                >
                  <ParagraphContent
                    paragraph={paragraph}
                    highlights={highlightsByParagraph.get(paragraph.id) ?? []}
                    focusedHighlightId={focusedHighlightId}
                  />
                </article>
              );
            })}
          </div>
          {selection !== null && (
            <div
              className="reader-selection"
              style={{ left: selection.left, top: selection.top }}
            >
              <button type="button" onClick={() => void saveHighlight()}>
                保存划线
              </button>
              <button type="button" onClick={() => setSelection(null)}>
                取消
              </button>
            </div>
          )}
        </div>
      </div>
      <footer className="reader__footer">
        <button
          type="button"
          className="reader-tool reader-tool--icon"
          aria-label="上一页"
          title="上一页"
          onClick={() => void syncPosition(userPosition - 1)}
          disabled={userPosition <= 1}
        >
          ←
        </button>
        <span className="reader__page-number">
          {pages.length === 0 ? 1 : pageIndex + 1} / {Math.max(1, pages.length)}
        </span>
        <button
          type="button"
          className="reader-tool reader-tool--icon"
          aria-label="下一页"
          title="下一页"
          onClick={() => void syncPosition(userPosition + 1)}
          disabled={userPosition >= totalParagraphs}
        >
          →
        </button>
        <div className="reader__footer-spacer" />
        <button
          type="button"
          className="reader-tool"
          onClick={() => setMarkPanel("highlights")}
        >
          划线
        </button>
        <button
          type="button"
          className="reader-tool"
          onClick={() => setMarkPanel("bookmarks")}
        >
          书签
        </button>
        {readCount >= 1 && (
          <button
            type="button"
            className="reader-tool"
            onClick={() => void reread()}
          >
            重读
          </button>
        )}
        <button
          type="button"
          className="reader-tool"
          onClick={() => setNoteOpen(true)}
        >
          笔记
        </button>
      </footer>

      {noteOpen && <NotePanel onClose={() => setNoteOpen(false)} />}
      {markPanel === "highlights" && (
        <Modal title="划线" onClose={() => setMarkPanel(null)}>
          <input
            className="reader-search"
            type="search"
            aria-label="搜索划线"
            placeholder="搜索划线内容"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
          />
          {searchedHighlights.length === 0 ? (
            <p className="reader-mark-empty">没有匹配的划线。</p>
          ) : (
            <ul className="reader-mark-list">
              {searchedHighlights.map((note) => (
                <li key={note.id} className="reader-mark-item">
                  <button
                    type="button"
                    className="reader-mark-item__jump"
                    onClick={() => void visitHighlight(note)}
                  >
                    <span>第 {note.paragraph_index} 段</span>
                    <q>{note.selected_text}</q>
                  </button>
                  <button
                    type="button"
                    className="reader-mark-item__delete"
                    aria-label="删除划线"
                    title="删除划线"
                    onClick={() => void deleteNote(note.id)}
                  >
                    ×
                  </button>
                </li>
              ))}
            </ul>
          )}
        </Modal>
      )}
      {markPanel === "bookmarks" && (
        <Modal title="书签" onClose={() => setMarkPanel(null)}>
          {bookmarks.length === 0 ? (
            <p className="reader-mark-empty">还没有书签。</p>
          ) : (
            <ul className="reader-mark-list">
              {bookmarks.map((bookmark) => (
                <li key={bookmark.id} className="reader-mark-item">
                  <button
                    type="button"
                    className="reader-mark-item__jump"
                    onClick={() => {
                      setMarkPanel(null);
                      void jumpToPosition(bookmark.paragraph_index);
                    }}
                  >
                    <span>第 {bookmark.paragraph_index} 段</span>
                    <q>{bookmark.preview}</q>
                  </button>
                  <button
                    type="button"
                    className="reader-mark-item__delete"
                    aria-label="删除书签"
                    title="删除书签"
                    onClick={() => void toggleBookmark(bookmark.paragraph_id)}
                  >
                    ×
                  </button>
                </li>
              ))}
            </ul>
          )}
        </Modal>
      )}
    </div>
  );
}
