import readingPolicy from "../../../../nyx/settings/reading.json";
import { captureScope, isScopeCurrent } from "../../api/characterScope";
import { getBookParagraphs } from "../../api/client";
import { formatPolicyText } from "../../lib/policy";
import {
  createElement,
  Fragment,
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import {
  WINDOW_SIZE,
  nyxStatusOf,
  useReaderStore,
} from "../../stores/readerStore";
import { useCharacterStore } from "../../stores/characterStore";
import type {
  Paragraph,
  ParagraphBlock,
  TextMark,
  UserNoteWithAnnotations,
} from "../../types/api";
import Modal from "../layout/Modal";
import NotePanel from "./NotePanel";
import ReadingCompanion from "./ReadingCompanion";

type SelectionDraft = {
  paragraphId: string;
  text: string;
  start: number;
  end: number;
  left: number;
  top: number;
};

type MarkPanel = "highlights" | "bookmarks" | null;
export type ReaderPage = "reader" | "companion" | "contents" | "highlights" | "bookmarks" | "notes" | "new-note";

function Contents({ bookId, total, onJump, onClose }: {
  bookId: string; total: number; onJump: (index: number) => void; onClose: () => void;
}) {
  const [chapters, setChapters] = useState<Paragraph[]>([]);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  useEffect(() => {
    let cancelled = false;
    const scope = captureScope();
    async function load() {
      const found: Paragraph[] = [];
      for (let start = 1; start <= total; start += WINDOW_SIZE) {
        const items = await getBookParagraphs(bookId, start, Math.min(total, start + WINDOW_SIZE - 1));
        if (cancelled || !isScopeCurrent(scope)) return;
        found.push(...items.filter((item) => item.is_chapter_start || item.index === 1));
      }
      setChapters(found);
    }
    void load().catch((reason: unknown) => {
      if (!cancelled && isScopeCurrent(scope)) setError(reason instanceof Error ? reason.message : String(reason));
    }).finally(() => {
      if (!cancelled && isScopeCurrent(scope)) setLoading(false);
    });
    return () => { cancelled = true; };
  }, [bookId, total]);
  return <Modal title={readingPolicy.copy.ReaderView.mobile_contents} onClose={onClose} page>
    {loading && <p role="status">{readingPolicy.copy.ReaderView.mobile_loading}</p>}
    {error && <p role="alert">{error}</p>}
    <div className="nyx-mobile__contents">
      {chapters.map((chapter) => <button key={chapter.id} type="button" onClick={() => onJump(chapter.index)}>
        {chapter.text.split("\n", 1)[0]}
      </button>)}
    </div>
  </Modal>;
}

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

export default function ReaderView({ mobilePage, onMobilePage, onCloseBook }: {
  mobilePage?: ReaderPage;
  onMobilePage?: (page: ReaderPage) => void;
  onCloseBook?: () => void;
} = {}) {
  const mobile = mobilePage !== undefined;
  const toolPage = mobile && mobilePage !== "reader" && mobilePage !== "companion";
  const name = useCharacterStore((state) => state.registry?.characters.find((item) => item.id === state.registry?.current_id)?.name);
  const bookId = useReaderStore((state) => state.bookId);
  const books = useReaderStore((state) => state.books);
  const totalParagraphs = useReaderStore((state) => state.totalParagraphs);
  const paragraphs = useReaderStore((state) => state.paragraphs);
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
  const viewportRef = useRef<HTMLDivElement | null>(null);
  const paraRefs = useRef<Map<number, HTMLElement>>(new Map());
  const scrollAnchor = useRef<{
    bookId: string | null;
    position: number;
    paragraphs: Paragraph[];
    offset: number;
    scrollTop: number;
  } | null>(null);
  const [navigation, setNavigation] = useState(0);
  const [noteOpen, setNoteOpen] = useState(false);
  const [markPanel, setMarkPanel] = useState<MarkPanel>(null);
  const [toolsOpen, setToolsOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [selection, setSelection] = useState<SelectionDraft | null>(null);
  const [focusedHighlightId, setFocusedHighlightId] = useState<string | null>(
    null,
  );
  const closeTool = () => onMobilePage?.("reader");
  const setMarks = (value: MarkPanel) => {
    if (mobile) onMobilePage?.(value ?? "reader");
    else setMarkPanel(value);
  };
  const marks = mobile ? mobilePage : markPanel;

  useEffect(() => {
    void loadNotes();
    void loadBookmarks();
  }, [bookId, loadBookmarks, loadNotes]);

  useLayoutEffect(() => {
    const viewport = viewportRef.current;
    const paragraph = paraRefs.current.get(userPosition);
    if (toolPage || viewport === null || paragraph === undefined) return;
    const anchor = scrollAnchor.current;
    const samePosition = anchor?.bookId === bookId && anchor.position === userPosition;
    if (samePosition && anchor.paragraphs === paragraphs) return;
    // 替换段落窗口时，保留当前段在视口里的像素位置。
    const offset = samePosition ? anchor.offset : 0;
    const top = viewport.getBoundingClientRect().top;
    viewport.scrollTop += paragraph.getBoundingClientRect().top - top - offset;
    scrollAnchor.current = {
      bookId, position: userPosition, paragraphs,
      offset: paragraph.getBoundingClientRect().top - top,
      scrollTop: viewport.scrollTop,
    };
  }, [bookId, navigation, paragraphs, toolPage, userPosition]);

  const handleScroll = () => {
    const viewport = viewportRef.current;
    const anchor = scrollAnchor.current;
    if (toolPage || viewport === null || anchor?.bookId !== bookId ||
        anchor.position !== useReaderStore.getState().userPosition ||
        anchor.scrollTop === viewport.scrollTop) return;
    const top = viewport.getBoundingClientRect().top;
    let position = paragraphs[0]?.index;
    for (const { index } of paragraphs) {
      const paragraph = paraRefs.current.get(index);
      if (paragraph === undefined || paragraph.getBoundingClientRect().top > top + 1) break;
      position = index;
    }
    if (position === undefined) return;
    const paragraph = paraRefs.current.get(position);
    if (paragraph === undefined) return;
    scrollAnchor.current = {
      bookId, position, paragraphs,
      offset: paragraph.getBoundingClientRect().top - top,
      scrollTop: viewport.scrollTop,
    };
    setSelection(null);
    void syncPosition(position);
  };

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

  const handleSelection = useCallback(() => {
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
      anchorRoot !== focusRoot ||
      !viewportRef.current?.contains(anchorRoot)
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
      left: viewportRect === undefined ? 0 : Math.max(0, Math.min(rect.left - viewportRect.left, viewportRect.width / 2)),
      top: viewportRect === undefined ? 0 : Math.max(0, rect.bottom - viewportRect.top),
    });
  }, [paragraphs]);

  useEffect(() => {
    if (toolPage) return;
    document.addEventListener("selectionchange", handleSelection);
    return () => document.removeEventListener("selectionchange", handleSelection);
  }, [handleSelection, toolPage]);

  const clearSelection = () => {
    setSelection(null);
    window.getSelection()?.removeAllRanges();
  };

  const resetScrollAnchor = () => {
    clearSelection();
    scrollAnchor.current = null;
    setNavigation((value) => value + 1);
  };

  const moveTo = (position: number) => {
    resetScrollAnchor();
    return jumpToPosition(position);
  };

  const saveHighlight = async () => {
    if (selection === null || bookId === null) return;
    const scope = captureScope();
    const saved = await addNote({
      book_id: bookId,
      paragraph_id: selection.paragraphId,
      content: "",
      selected_text: selection.text,
      selection_start: selection.start,
      selection_end: selection.end,
    });
    if (saved && isScopeCurrent(scope)) {
      clearSelection();
    }
  };

  const visitHighlight = async (
    note: UserNoteWithAnnotations & { paragraph_index: number },
  ) => {
    setFocusedHighlightId(note.id);
    setMarks(null);
    await moveTo(note.paragraph_index);
  };

  const currentParagraph = paragraphs.find(
    (paragraph) => paragraph.index === userPosition,
  );
  const currentBookmark = bookmarks.find(
    (bookmark) => bookmark.paragraph_id === currentParagraph?.id,
  );
  const book = books.find((item) => item.id === bookId);
  const title = book?.title ?? readingPolicy.copy.ReaderView.text;
  const author = book?.author ?? "";
  const nyxStatus = nyxStatusOf(bookId, nyxPosition, userPosition);
  const nyxStatusText = nyxStatus === "reading" ? readingPolicy.copy.ReaderView.text_2 : readingPolicy.copy.ReaderView.text_3;

  const body = <>
      <header className="reader__header">
        <button
          type="button"
          className="reader-tool reader-tool--icon"
          aria-label={readingPolicy.copy.ReaderView.aria_label}
          title={readingPolicy.copy.ReaderView.aria_label}
          onClick={onCloseBook ?? closeBook}
        >
          ←
        </button>
        <div className="reader__identity">
          <strong className="reader__title">{title}</strong>
          {author !== "" && <span className="reader__author">{author}</span>}
        </div>
        {mobile && <button type="button" className="reader-tool" onClick={() => onMobilePage?.("contents")}>
          {readingPolicy.copy.ReaderView.mobile_contents}
        </button>}
        {!mobile && <span className="reader__pos">
          {nyxStatusText}{readingPolicy.copy.ReaderView.span}{nyxPosition}{readingPolicy.copy.ReaderView.span_2}{userPosition} / {totalParagraphs} 段
        </span>}
        <button
          type="button"
          className="reader-tool reader-tool--icon"
          aria-label={currentBookmark === undefined ? readingPolicy.copy.ReaderView.text_4 : readingPolicy.copy.ReaderView.text_5}
          title={currentBookmark === undefined ? readingPolicy.copy.ReaderView.text_4 : readingPolicy.copy.ReaderView.text_5}
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
        <div className="reader__viewport">
          <div
            className="reader-text"
            ref={viewportRef}
            tabIndex={0}
            aria-label={readingPolicy.copy.ReaderView.aria_label_text}
            onScroll={handleScroll}
            onMouseUp={handleSelection}
          >
            <div className="reader-text__content">
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
                  >
                    {mobile && paragraph.index === nyxPosition && <span className="reader__nyx-marker">
                      {name ? formatPolicyText(readingPolicy.templates.Mobile.position_here, { p0: name }) : readingPolicy.copy.ReaderView.mobile_position}
                    </span>}
                    <ParagraphContent
                      paragraph={paragraph}
                      highlights={highlightsByParagraph.get(paragraph.id) ?? []}
                      focusedHighlightId={focusedHighlightId}
                    />
                  </article>
                );
              })}
            </div>
          </div>
          {selection !== null && (
            <div
              className="reader-selection"
              style={{ left: selection.left, top: `clamp(0px, ${selection.top}px, calc(100% - 2.5rem))` }}
              onMouseDown={(event) => event.preventDefault()}
            >
              <button type="button" onClick={() => void saveHighlight()}>
                {readingPolicy.copy.ReaderView.save_highlight}
              </button>
              <button type="button" onClick={clearSelection}>
                {readingPolicy.copy.ReaderView.cancel_highlight}
              </button>
            </div>
          )}
        </div>
      </div>
      {mobile && nyxPosition !== userPosition && <button type="button" className="reader__nyx-position"
        onClick={() => void moveTo(nyxPosition)}>
        {formatPolicyText(readingPolicy.templates.Mobile.position, { p0: name ?? readingPolicy.copy.ReaderView.mobile_name, p1: nyxPosition })}
      </button>}
      <footer className="reader__footer">
        {mobile && totalParagraphs > 0 && <span className="reader__pos">
          {formatPolicyText(readingPolicy.templates.reader_progress, { p0: userPosition, p1: totalParagraphs })}
        </span>}
        <div className="reader__footer-spacer" />
        <div className="reader-tools" onBlur={(event) => {
          if (!event.currentTarget.contains(event.relatedTarget)) setToolsOpen(false);
        }} onKeyDown={(event) => {
          if (event.key === "Escape") {
            setToolsOpen(false);
            event.currentTarget.querySelector("button")?.focus();
          }
        }}>
          <button type="button" className="reader-tool reader-tool--icon" aria-label={readingPolicy.copy.ReaderView.aria_label_4} title={readingPolicy.copy.ReaderView.aria_label_4} aria-expanded={toolsOpen} onClick={() => setToolsOpen((open) => !open)}>⋮</button>
          {toolsOpen && <div className="reader-tools__menu">
            <button type="button" onClick={() => { setMarks("highlights"); setToolsOpen(false); }}>{readingPolicy.copy.ReaderView.button}</button>
            <button type="button" onClick={() => { setMarks("bookmarks"); setToolsOpen(false); }}>{readingPolicy.copy.ReaderView.button_2}</button>
            {readCount >= 1 && <button type="button" onClick={() => {
              resetScrollAnchor();
              void reread();
              setToolsOpen(false);
            }}>{readingPolicy.copy.ReaderView.button_3}</button>}
            <button type="button" onClick={() => { if (mobile) onMobilePage?.("notes"); else setNoteOpen(true); setToolsOpen(false); }}>{readingPolicy.copy.ReaderView.button_4}</button>
          </div>}
        </div>
      </footer>

  </>;
  return (
    <div className={mobile ? "nyx-mobile__reader" : "reader"}>
      {mobile ? <div className="reader" hidden={toolPage}>{body}</div> : body}
      {mobile && bookId && !toolPage && <ReadingCompanion key={bookId} bookId={bookId} open={mobilePage === "companion"}
        onToggle={() => onMobilePage?.(mobilePage === "companion" ? "reader" : "companion")} />}
      {mobilePage === "contents" && bookId && <Contents bookId={bookId} total={totalParagraphs} onClose={closeTool}
        onJump={(index) => { closeTool(); void moveTo(index); }} />}
      {(noteOpen || mobilePage === "notes" || mobilePage === "new-note") && <NotePanel
        page={mobile}
        writeOnly={mobilePage === "new-note"}
        onWrite={mobile && mobilePage === "notes" ? () => onMobilePage?.("new-note") : undefined}
        onClose={mobilePage === "new-note" ? () => onMobilePage?.("notes") : mobile ? closeTool : () => setNoteOpen(false)}
      />}
      {marks === "highlights" && (
        <Modal title={readingPolicy.copy.ReaderView.button} page={mobile} onClose={() => setMarks(null)}>
          <input
            className="reader-search"
            type="search"
            aria-label={readingPolicy.copy.ReaderView.aria_label_5}
            placeholder={readingPolicy.copy.ReaderView.placeholder}
            value={query}
            onChange={(event) => setQuery(event.target.value)}
          />
          {searchedHighlights.length === 0 ? (
            <p className="reader-mark-empty">{readingPolicy.copy.ReaderView.p}</p>
          ) : (
            <ul className="reader-mark-list">
              {searchedHighlights.map((note) => (
                <li key={note.id} className="reader-mark-item">
                  <button
                    type="button"
                    className="reader-mark-item__jump"
                    onClick={() => void visitHighlight(note)}
                  >
                    <span>{readingPolicy.copy.ReaderView.span_3}{note.paragraph_index}{readingPolicy.copy.ReaderView.span_4}</span>
                    <q>{note.selected_text}</q>
                  </button>
                  <button
                    type="button"
                    className="reader-mark-item__delete"
                    aria-label={readingPolicy.copy.ReaderView.aria_label_6}
                    title={readingPolicy.copy.ReaderView.aria_label_6}
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
      {marks === "bookmarks" && (
        <Modal title={readingPolicy.copy.ReaderView.button_2} page={mobile} onClose={() => setMarks(null)}>
          {bookmarks.length === 0 ? (
            <p className="reader-mark-empty">{readingPolicy.copy.ReaderView.p_2}</p>
          ) : (
            <ul className="reader-mark-list">
              {bookmarks.map((bookmark) => (
                <li key={bookmark.id} className="reader-mark-item">
                  <button
                    type="button"
                    className="reader-mark-item__jump"
                    onClick={() => {
                      setMarks(null);
                      void moveTo(bookmark.paragraph_index);
                    }}
                  >
                    <span>{readingPolicy.copy.ReaderView.span_3}{bookmark.paragraph_index}{readingPolicy.copy.ReaderView.span_4}</span>
                    <q>{bookmark.preview}</q>
                  </button>
                  <button
                    type="button"
                    className="reader-mark-item__delete"
                    aria-label={readingPolicy.copy.ReaderView.aria_label_7}
                    title={readingPolicy.copy.ReaderView.aria_label_7}
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
