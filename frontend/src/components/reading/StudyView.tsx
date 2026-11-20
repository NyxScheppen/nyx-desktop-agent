import { useEffect, useRef, useState, type ReactNode } from "react";
import policy from "../../../../nyx/settings/reading.json";
import { captureScope, isScopeCurrent } from "../../api/characterScope";
import { getOpenStudy } from "../../api/client";
import { useReaderStore } from "../../stores/readerStore";
import { useStudyStore } from "../../stores/studyStore";
import type {
  StudyAnnotation,
  StudyGraph,
  StudyNode,
} from "../../types/readingStudy";
import Modal from "../layout/Modal";
import BookshelfView from "./BookshelfView";
import ReaderView from "./ReaderView";
import StudyMarkdown, { markdownBlocks } from "./StudyMarkdown";
import { StudyResults } from "./StudyResults";
import "./study.css";

const copy = policy.study.copy;
const parameters = policy.study.parameters;

function StudyDialog({
  title,
  onClose,
  children,
}: {
  title: string;
  onClose: () => void;
  children: ReactNode;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const close = useRef(onClose);
  close.current = onClose;
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    const dialog = ref.current?.closest<HTMLElement>("[role=dialog]");
    const focusable = () =>
      Array.from(
        dialog?.querySelectorAll<HTMLElement>(
          "button:not(:disabled), a[href], textarea, input, [tabindex='0']",
        ) ?? [],
      );
    focusable()[0]?.focus();
    const key = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        close.current();
      }
      if (event.key === "Tab") {
        const elements = focusable();
        if (!elements.length) return;
        const first = elements[0],
          last = elements[elements.length - 1];
        if (event.shiftKey && document.activeElement === first) {
          event.preventDefault();
          last.focus();
        } else if (!event.shiftKey && document.activeElement === last) {
          event.preventDefault();
          first.focus();
        }
      }
    };
    document.addEventListener("keydown", key);
    return () => {
      document.removeEventListener("keydown", key);
      previous?.focus();
    };
  }, []);
  return (
    <Modal title={title} onClose={onClose}>
      <div ref={ref} className="study-dialog">
        {children}
      </div>
    </Modal>
  );
}

function NoteDocument({ onAttach }: { onAttach: (text: string) => void }) {
  const draft = useStudyStore((s) => s.noteDraft);
  const note = useStudyStore((s) => s.note);
  const error = useStudyStore((s) => s.noteError);
  const saving = useStudyStore((s) => s.saving);
  const edit = useStudyStore((s) => s.editNote);
  const save = useStudyStore((s) => s.saveNote);
  const [blocks, setBlocks] = useState(() => markdownBlocks(draft));
  const [active, setActive] = useState<number | null>(null);
  const mounted = useRef(draft);
  useEffect(() => {
    if (mounted.current !== draft && active === null)
      setBlocks(markdownBlocks(draft));
    mounted.current = draft;
    if (draft === note?.content || error) return;
    const timer = setTimeout(() => void save(), parameters.draft_save_ms);
    return () => clearTimeout(timer);
  }, [draft, note?.content, error, active, save]);
  return (
    <div className="study-note-document">
      <div className="study-note-actions">
        <small role="status">
          {error
            ? copy.unsaved
            : saving
              ? copy.saving
              : draft === note?.content
                ? copy.saved
                : copy.unsaved}
        </small>
        <button
          className="reading-btn"
          onClick={() => onAttach(draft)}
          disabled={!draft.trim()}
        >
          {copy.attach}
        </button>
      </div>
      {error && (
        <div role="alert">
          <p>{error}</p>
          <button className="reading-btn" onClick={() => void save()}>
            {copy.retry_save}
          </button>
          <button
            className="reading-btn"
            onClick={() => void useStudyStore.getState().reloadNote()}
          >
            {copy.reload}
          </button>
        </div>
      )}
      {blocks.map((block, index) =>
        active === index ? (
          <textarea
            key={index}
            className="study-note-edit"
            autoFocus
            aria-label={copy.notes}
            value={block}
            onChange={(event) => {
              const next = blocks.map((value, i) =>
                i === index ? event.target.value : value,
              );
              setBlocks(next);
              edit(next.join("\n\n"));
            }}
            onBlur={() => {
              setActive(null);
              setBlocks(markdownBlocks(useStudyStore.getState().noteDraft));
            }}
            onKeyDown={(event) => {
              if (event.key === "Escape" && !event.nativeEvent.isComposing)
                event.currentTarget.blur();
            }}
          />
        ) : (
          <div
            key={index}
            role="button"
            tabIndex={0}
            className="study-note-block"
            onClick={(event) => {
              if (!(
                event.target instanceof Element && event.target.closest("a")
              ))
                setActive(index);
            }}
            onKeyDown={(event) => {
              if (event.key === "Enter" || event.key === " ") {
                event.preventDefault();
                setActive(index);
              }
            }}
          >
            {block ? (
              <StudyMarkdown text={block} />
            ) : (
              <p className="muted">{copy.note_placeholder}</p>
            )}
          </div>
        ),
      )}
      <button
        className="study-note-add"
        onClick={() => {
          setBlocks([...blocks, ""]);
          setActive(blocks.length);
        }}
      >
        {copy.more_note}
      </button>
    </div>
  );
}

function GraphCanvas({
  graph,
  onSelect,
}: {
  graph: StudyGraph;
  onSelect: (node: StudyNode) => void;
}) {
  const [camera, setCamera] = useState({ x: 0, y: 0, scale: 1 });
  const drag = useRef<{ x: number; y: number; moved: boolean } | null>(null);
  const cols = Math.max(1, Math.ceil(Math.sqrt(graph.nodes.length)));
  const width = Math.max(600, cols * 220),
    height = Math.max(360, Math.ceil(graph.nodes.length / cols) * 125);
  const indices = new Map(graph.nodes.map((node, i) => [node.id, i]));
  const point = (id: string) => {
    const i = indices.get(id)!;
    return { x: (i % cols) * 220 + 110, y: Math.floor(i / cols) * 125 + 65 };
  };
  return (
    <div className="study-graph-canvas">
      <div className="study-graph-controls">
        <button
          onClick={() => setCamera((c) => ({ ...c, scale: c.scale * 1.2 }))}
          aria-label={copy.map}
        >
          ＋
        </button>
        <button
          onClick={() => setCamera((c) => ({ ...c, scale: c.scale / 1.2 }))}
          aria-label={copy.map}
        >
          −
        </button>
        <button onClick={() => setCamera({ x: 0, y: 0, scale: 1 })}>⌂</button>
      </div>
      <svg
        viewBox={`0 0 ${width} ${height}`}
        role="group"
        aria-label={copy.map}
        onPointerDown={(event) => {
          drag.current = { x: event.clientX, y: event.clientY, moved: false };
        }}
        onPointerMove={(event) => {
          const start = drag.current;
          if (!start || !event.buttons) return;
          const dx = event.clientX - start.x,
            dy = event.clientY - start.y;
          if (Math.hypot(dx, dy) > parameters.drag_threshold) {
            start.moved = true;
            event.currentTarget.setPointerCapture(event.pointerId);
          }
          if (start.moved) {
            const x = (dx * width) / event.currentTarget.clientWidth,
              y = (dy * height) / event.currentTarget.clientHeight;
            setCamera((c) => ({
              ...c,
              x: c.x + x,
              y: c.y + y,
            }));
            start.x = event.clientX;
            start.y = event.clientY;
          }
        }}
        onPointerUp={(event) => {
          if (event.currentTarget.hasPointerCapture(event.pointerId))
            event.currentTarget.releasePointerCapture(event.pointerId);
        }}
      >
        <g
          transform={`translate(${camera.x},${camera.y}) scale(${camera.scale})`}
        >
          {graph.edges.map((edge, i) => {
            const a = point(edge.from),
              b = point(edge.to);
            return (
              <g key={i}>
                <line x1={a.x} y1={a.y} x2={b.x} y2={b.y} />
                <text x={(a.x + b.x) / 2} y={(a.y + b.y) / 2}>
                  {edge.label}
                </text>
              </g>
            );
          })}
          {graph.nodes.map((node) => {
            const p = point(node.id);
            return (
              <g
                key={node.id}
                tabIndex={0}
                role="button"
                aria-label={node.title}
                className={`study-graph-node study-graph-node--${node.state}`}
                transform={`translate(${p.x},${p.y})`}
                onClick={() => {
                  if (!drag.current?.moved) onSelect(node);
                }}
                onKeyDown={(event) => {
                  if (event.key === "Enter") onSelect(node);
                }}
              >
                <rect x={-95} y={-35} width={190} height={70} rx={12} />
                <text y={-4}>
                  {node.title.length > 12
                    ? `${node.title.slice(0, 12)}…`
                    : node.title}
                </text>
                <text y={19} className="study-graph-state">
                  {copy[node.state]}
                </text>
              </g>
            );
          })}
        </g>
      </svg>
    </div>
  );
}

export default function StudyView({ onClose }: { onClose: () => void }) {
  const snapshot = useStudyStore((s) => s.snapshot);
  const entering = useStudyStore((s) => s.entering);
  const error = useStudyStore((s) => s.error);
  const pendingInput = useStudyStore((s) => s.pendingInput);
  const sending = useStudyStore((s) => s.sending);
  const [tab, setTab] = useState<"notes" | "map">("notes");
  const [collapsed, setCollapsed] = useState(false);
  const [dialog, setDialog] = useState<
    "map" | "history" | "reader" | "annotation" | "results" | "leave" | null
  >(null);
  const [selectedNode, setSelectedNode] = useState<StudyNode | null>(null);
  const [draft, setDraft] = useState("");
  const [attachments, setAttachments] = useState<string[]>([]);
  const [annotation, setAnnotation] = useState<StudyAnnotation | null>(null);
  const [selection, setSelection] = useState<{
    id: string;
    quote: string;
  } | null>(null);
  const [comment, setComment] = useState("");
  const [commentQuote, setCommentQuote] = useState<{
    id: string;
    quote: string;
  } | null>(null);
  const scroll = useRef<HTMLDivElement>(null);
  const scope = useRef(captureScope());
  useEffect(() => {
    let activeView = true;
    const book =
      useStudyStore.getState().snapshot?.book.id ??
      useReaderStore.getState().bookId;
    if (book) void useStudyStore.getState().open(book);
    else
      void getOpenStudy()
        .then((active) => {
          if (activeView && active && isScopeCurrent(scope.current))
            void useStudyStore.getState().open(active.book.id);
        })
        .catch((error: unknown) => {
          if (activeView && isScopeCurrent(scope.current))
            useStudyStore.setState({ error: String(error) });
        });
    const timer = setInterval(
      () => void useStudyStore.getState().refresh(),
      parameters.poll_ms,
    );
    const heartbeat = setInterval(
      () => void useStudyStore.getState().heartbeat(),
      parameters.heartbeat_ms,
    );
    const detach = () => {
      if (isScopeCurrent(scope.current)) useStudyStore.getState().detach();
    };
    window.addEventListener("pagehide", detach);
    return () => {
      activeView = false;
      clearInterval(timer);
      clearInterval(heartbeat);
      window.removeEventListener("pagehide", detach);
      detach();
    };
  }, []);
  useEffect(() => {
    scroll.current?.scrollTo({ top: scroll.current.scrollHeight });
  }, [snapshot?.session.turns.map((t) => `${t.id}:${t.status}`).join(",")]);
  const showSource = async (index: number) => {
    const current = captureScope();
    if (!snapshot) return;
    if (useReaderStore.getState().bookId !== snapshot.book.id)
      await useReaderStore.getState().openBook(snapshot.book.id);
    if (!isScopeCurrent(current)) return;
    await useReaderStore.getState().jumpToPosition(index);
    if (isScopeCurrent(current)) setDialog("reader");
  };
  const send = async () => {
    const current = captureScope();
    const input = pendingInput ?? {
      input_id: crypto.randomUUID(),
      message: draft,
      notes: [...attachments],
      annotation,
      presentation: "",
    };
    if (
      (await useStudyStore.getState().send(input)) &&
      isScopeCurrent(current)
    ) {
      setDraft((latest) => (latest === input.message ? "" : latest));
      setAttachments((latest) =>
        JSON.stringify(latest) === JSON.stringify(input.notes) ? [] : latest,
      );
      setAnnotation((latest) =>
        JSON.stringify(latest) === JSON.stringify(input.annotation)
          ? null
          : latest,
      );
    }
  };
  const leave = async (keepDraft = false) => {
    const current = captureScope();
    const ok = await useStudyStore.getState().leave(keepDraft);
    if (!isScopeCurrent(current)) return;
    if (ok) onClose();
    else if (useStudyStore.getState().noteError) setDialog("leave");
  };
  const choose = (bookId: string) => void useStudyStore.getState().open(bookId);
  if (!snapshot)
    return (
      <section className="study-page study-page--choose">
        <header>
          <h2>{copy.choose}</h2>
          <button className="reading-btn" onClick={onClose}>
            {copy.back}
          </button>
        </header>
        {error && <p role="alert">{error}</p>}
        {entering ? (
          <p role="status">{copy.prepare}</p>
        ) : (
          <BookshelfView onChoose={choose} />
        )}
      </section>
    );
  const prepared = snapshot.materials.filter(
    (m) => m.state === "completed",
  ).length;
  const failed =
    snapshot.feedback_jobs.some((job) => job.state === "failed") ||
    snapshot.materials.some((m) => m.state === "failed") ||
    snapshot.graph_work.state === "failed" ||
    snapshot.sessions.some((s) =>
      Object.values(s.results).some((r) => r.state === "failed"),
    );
  return (
    <section
      className={`study-page${collapsed ? " study-page--collapsed" : ""}`}
    >
      <header className="study-header">
        <div>
          <h2>{snapshot.book.title}</h2>
          <span>{snapshot.book.author}</span>
        </div>
        <div>
          <button
            className="reading-btn"
            onClick={() =>
              void showSource(
                useReaderStore.getState().bookId === snapshot.book.id
                  ? useReaderStore.getState().userPosition
                  : 1,
              )
            }
          >
            {copy.read}
          </button>
          <button className="reading-btn" onClick={() => setDialog("results")}>
            {copy.results}
          </button>
          <button className="reading-btn" onClick={() => void leave()}>
            {copy.leave}
          </button>
        </div>
      </header>
      <main className="study-conversation">
        {error && <p role="alert">{error}</p>}
        <div className="study-turns" ref={scroll}>
          {snapshot.session.turns.map((turn) => (
            <div key={turn.id} className="study-turn">
              <div className="study-user">
                <StudyMarkdown text={turn.input.message} />
                {turn.input.notes.map((note, i) => (
                  <details key={i}>
                    <summary>{copy.note_attachment}</summary>
                    <StudyMarkdown text={note} />
                  </details>
                ))}
                {turn.input.annotation && (
                  <blockquote>
                    {turn.input.annotation.quote}
                    <p>{turn.input.annotation.comment}</p>
                  </blockquote>
                )}
              </div>
              {turn.status === "committed" ? (
                turn.reply && (
                  <div className="study-answer">
                    <div
                      data-study-reply={turn.id}
                      onMouseUp={() => {
                        const value = window.getSelection();
                        const a =
                          value?.anchorNode?.parentElement?.closest<HTMLElement>(
                            "[data-study-reply]",
                          );
                        const b =
                          value?.focusNode?.parentElement?.closest<HTMLElement>(
                            "[data-study-reply]",
                          );
                        const quote = value?.toString().trim() ?? "";
                        setSelection(
                          quote && a === b && a?.dataset.studyReply === turn.id
                            ? { id: turn.id, quote }
                            : null,
                        );
                      }}
                    >
                      <StudyMarkdown
                        text={turn.reply}
                        onSource={(index) => void showSource(index)}
                      />
                    </div>
                    {selection?.id === turn.id && (
                      <button
                        className="reading-btn"
                        onMouseDown={(event) => event.preventDefault()}
                        onClick={() => {
                          setCommentQuote(selection);
                          setComment("");
                          setDialog("annotation");
                        }}
                      >
                        {copy.annotation}
                      </button>
                    )}
                    <button
                      className="reading-btn study-note-append"
                      onClick={() => {
                        const store = useStudyStore.getState();
                        store.editNote(
                          `${store.noteDraft}${store.noteDraft ? "\n\n" : ""}${turn.reply}`,
                        );
                        setTab("notes");
                        setCollapsed(false);
                      }}
                    >
                      {copy.add_note}
                    </button>
                  </div>
                )
              ) : (
                <p role="status">
                  {turn.status === "failed"
                    ? turn.error
                    : turn.status === "incomplete"
                      ? copy.incomplete
                      : copy.queued}
                  {(turn.status === "failed" || turn.status === "queued") && (
                    <button
                      className="reading-btn"
                      onClick={() =>
                        void useStudyStore.getState().retryTurn(turn.id)
                      }
                    >
                      {copy.retry_turn}
                    </button>
                  )}
                </p>
              )}
            </div>
          ))}
        </div>
        <form
          className="study-input"
          onSubmit={(event) => {
            event.preventDefault();
            void send();
          }}
        >
          {attachments.map((text, i) => (
            <div className="study-attachment" key={i}>
              <span>
                {copy.note_attachment} · {text.length}
              </span>
              <button
                type="button"
                onClick={() =>
                  setAttachments((all) => all.filter((_, index) => index !== i))
                }
              >
                {copy.remove}
              </button>
            </div>
          ))}
          {annotation && (
            <div className="study-attachment">
              <blockquote>
                {annotation.quote}
                <p>{annotation.comment}</p>
              </blockquote>
              <button type="button" onClick={() => setAnnotation(null)}>
                {copy.remove}
              </button>
            </div>
          )}
          <textarea
            placeholder={copy.placeholder}
            aria-label={copy.placeholder}
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
          />
          <button
            className="reading-btn"
            disabled={
              sending ||
              (!pendingInput &&
                !draft.trim() &&
                !attachments.length &&
                !annotation)
            }
          >
            {pendingInput ? copy.retry : copy.send}
          </button>
        </form>
      </main>
      <aside className="study-sidebar">
        <nav>
          <button
            className={tab === "notes" ? "active" : ""}
            onClick={() => {
              setTab("notes");
              setCollapsed(false);
            }}
          >
            {copy.notes}
          </button>
          <button
            className={tab === "map" ? "active" : ""}
            onClick={() => {
              setTab("map");
              setCollapsed(false);
            }}
          >
            {copy.map}
          </button>
          <button
            onClick={() => setCollapsed(!collapsed)}
            aria-label={copy.close}
          >
            {collapsed ? "‹" : "›"}
          </button>
        </nav>
        {!collapsed &&
          (tab === "notes" ? (
            <NoteDocument
              onAttach={(text) => setAttachments((all) => [...all, text])}
            />
          ) : (
            <div className="study-map-preview">
              <p role="status">
                {copy.coverage_label
                  .replace("{done}", String(prepared))
                  .replace("{total}", String(snapshot.materials.length))}
                {snapshot.pending_evidence ? ` · ${copy.pending}` : ""}
              </p>
              <div
                role="button"
                tabIndex={0}
                className="study-map-thumbnail"
                onClick={() => setDialog("map")}
                onKeyDown={(event) => {
                  if (event.key === "Enter") setDialog("map");
                }}
              >
                {snapshot.graph.nodes.length ? (
                  <GraphCanvas
                    graph={snapshot.graph}
                    onSelect={() => setDialog("map")}
                  />
                ) : (
                  <p>{copy.empty_map}</p>
                )}
              </div>
              <button
                className="reading-btn"
                onClick={() => setDialog("history")}
              >
                {copy.history}
              </button>
              {failed && (
                <>
                  <p role="alert">{copy.failure}</p>
                  <button
                    className="reading-btn"
                    onClick={() => void useStudyStore.getState().retry()}
                  >
                    {copy.retry}
                  </button>
                </>
              )}
            </div>
          ))}
      </aside>
      {dialog === "map" && (
        <StudyDialog
          title={copy.map_title}
          onClose={() => {
            setDialog(null);
            setSelectedNode(null);
          }}
        >
          <GraphCanvas graph={snapshot.graph} onSelect={setSelectedNode} />
          {selectedNode && (
            <article className="study-node-detail">
              <h3>{selectedNode.title}</h3>
              <p>{copy[selectedNode.state]}</p>
              <StudyMarkdown text={selectedNode.content} />
              {selectedNode.sources.map((source, i) => (
                <blockquote key={i}>
                  {source.quote}
                  <button
                    className="study-source"
                    onClick={() => void showSource(source.index)}
                  >
                    {copy.source_label.replace("{index}", String(source.index))}
                  </button>
                </blockquote>
              ))}
              <details>
                <summary>{copy.evidence}</summary>
                {selectedNode.evidence.map((id) => {
                  const source = snapshot.evidence.find(
                    (turn) => turn.id === id,
                  );
                  return (
                    source && (
                      <blockquote key={id}>
                        <StudyMarkdown text={source.input.message} />
                        {source.input.annotation && (
                          <p>
                            {source.input.annotation.quote} —{" "}
                            {source.input.annotation.comment}
                          </p>
                        )}
                        {source.input.notes.map((note, i) => (
                          <StudyMarkdown key={i} text={note} />
                        ))}
                      </blockquote>
                    )
                  );
                })}
              </details>
            </article>
          )}
        </StudyDialog>
      )}
      {dialog === "history" && (
        <StudyDialog title={copy.history_title} onClose={() => setDialog(null)}>
          {snapshot.history.length ? (
            [...snapshot.history].reverse().map((entry) => (
              <article key={entry.id}>
                <h3>
                  v{entry.version} ·{" "}
                  {new Date(entry.created_at * 1000).toLocaleString()}
                </h3>
                <p>{entry.reason ?? copy.source}</p>
                {entry.operations.map((operation, i) => (
                  <div key={i}>
                    <h4>
                      {operation.op === "delete"
                        ? copy.delete_node
                        : operation.title}
                    </h4>
                    {operation.content && (
                      <StudyMarkdown text={operation.content} />
                    )}
                    {operation.sources?.map((source, j) => (
                      <blockquote key={j}>
                        {source.quote}
                        <button
                          className="study-source"
                          onClick={() => void showSource(source.index)}
                        >
                          {copy.source_label.replace(
                            "{index}",
                            String(source.index),
                          )}
                        </button>
                      </blockquote>
                    ))}
                  </div>
                ))}
              </article>
            ))
          ) : (
            <p>{copy.empty_history}</p>
          )}
        </StudyDialog>
      )}
      {dialog === "reader" && (
        <StudyDialog
          title={snapshot.book.title}
          onClose={() => setDialog(null)}
        >
          <ReaderView
            onExcerpt={(text) => {
              const store = useStudyStore.getState();
              store.editNote(
                `${store.noteDraft}${store.noteDraft ? "\n\n" : ""}> ${text}`,
              );
              setTab("notes");
              setDialog(null);
            }}
          />
        </StudyDialog>
      )}
      {dialog === "annotation" && commentQuote && (
        <StudyDialog
          title={copy.selection_title}
          onClose={() => setDialog(null)}
        >
          <blockquote>{commentQuote.quote}</blockquote>
          <textarea
            className="study-note-edit"
            aria-label={copy.comment}
            value={comment}
            onChange={(event) => setComment(event.target.value)}
          />
          <button
            className="reading-btn"
            disabled={!comment.trim()}
            onClick={() => {
              setAnnotation({
                reply_event_id: commentQuote.id,
                quote: commentQuote.quote,
                comment,
              });
              setDialog(null);
              setSelection(null);
            }}
          >
            {copy.bring}
          </button>
        </StudyDialog>
      )}
      {dialog === "results" && (
        <StudyDialog title={copy.results} onClose={() => setDialog(null)}>
          <StudyResults sessions={snapshot.sessions} />
          {snapshot.feedback_jobs
            .filter((job) => job.error)
            .map((job, i) => (
              <p role="alert" key={i}>
                {job.error}
              </p>
            ))}
          {failed && (
            <button
              className="reading-btn"
              onClick={() => void useStudyStore.getState().retry()}
            >
              {copy.retry}
            </button>
          )}
        </StudyDialog>
      )}
      {dialog === "leave" && (
        <StudyDialog title={copy.unsaved} onClose={() => setDialog(null)}>
          <p>{copy.save_before_leave}</p>
          <button className="reading-btn" onClick={() => void leave()}>
            {copy.retry_save}
          </button>
          <button className="reading-btn" onClick={() => void leave(true)}>
            {copy.retain_draft}
          </button>
        </StudyDialog>
      )}
    </section>
  );
}
