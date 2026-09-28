import { useEffect, useMemo, useState, type FormEvent } from "react";
import { getBookParagraphs } from "../../api/client";
import { formatResult } from "../../lib/activityResult";
import {
  ACTIVITY_STATUS_LABELS,
  ACTIVITY_TYPE_LABELS,
  ASSIGNED_TASK_STATUS_LABELS,
} from "../../lib/labels";
import { useActivityStore } from "../../stores/activityStore";
import { useReaderStore } from "../../stores/readerStore";
import type { AssignedTask, BookListItem, Paragraph } from "../../types/api";
import Panel from "../layout/Panel";

function timeLabel(ts: number): string {
  return new Date(ts * 1000).toLocaleTimeString();
}

function taskTarget(task: AssignedTask, books: BookListItem[]): string {
  if (task.type === "web") return task.url ?? "网页";
  const book = books.find((item) => item.id === task.book_id);
  const name = book?.title ?? task.book_id ?? "已删除书籍";
  return task.target_paragraph === null
    ? name
    : `${name} · 第 ${task.target_paragraph} 段`;
}

export default function ActivityPanel() {
  const data = useActivityStore((state) => state.data);
  const results = useActivityStore((state) => state.results);
  const tasks = useActivityStore((state) => state.tasks);
  const error = useActivityStore((state) => state.error);
  const taskError = useActivityStore((state) => state.taskError);
  const creatingTask = useActivityStore((state) => state.creatingTask);
  const refresh = useActivityStore((state) => state.refresh);
  const assignWebTask = useActivityStore((state) => state.createWebTask);
  const assignBookTask = useActivityStore((state) => state.createBookTask);
  const books = useReaderStore((state) => state.books);
  const booksError = useReaderStore((state) => state.booksError);
  const loadBooks = useReaderStore((state) => state.loadBooks);

  const [mode, setMode] = useState<"web" | "book">("web");
  const [url, setUrl] = useState("");
  const [bookId, setBookId] = useState("");
  const [target, setTarget] = useState("1");
  const [preview, setPreview] = useState<Paragraph[]>([]);
  const [previewError, setPreviewError] = useState<string | null>(null);
  const [formError, setFormError] = useState<string | null>(null);

  useEffect(() => {
    void refresh();
    void loadBooks();
  }, [loadBooks, refresh]);

  useEffect(() => {
    if (books.length === 0) {
      setBookId("");
      return;
    }
    if (books.some((book) => book.id === bookId)) return;
    const first = books[0];
    setBookId(first.id);
    setTarget(String(Math.max(1, first.user_position)));
  }, [bookId, books]);

  const selectedBook = useMemo(
    () => books.find((book) => book.id === bookId) ?? null,
    [bookId, books],
  );
  const targetParagraph = Number(target);
  const targetIsValid =
    selectedBook !== null &&
    Number.isInteger(targetParagraph) &&
    targetParagraph >= 1 &&
    targetParagraph <= selectedBook.total_paragraphs;

  useEffect(() => {
    if (mode !== "book" || selectedBook === null || !targetIsValid) {
      setPreview([]);
      setPreviewError(null);
      return;
    }
    let active = true;
    setPreviewError(null);
    void getBookParagraphs(
      selectedBook.id,
      Math.max(1, targetParagraph - 1),
      Math.min(selectedBook.total_paragraphs, targetParagraph + 1),
    )
      .then((paragraphs) => {
        if (active) setPreview(paragraphs);
      })
      .catch((reason: unknown) => {
        if (!active) return;
        setPreview([]);
        setPreviewError(reason instanceof Error ? reason.message : String(reason));
      });
    return () => {
      active = false;
    };
  }, [mode, selectedBook, targetIsValid, targetParagraph]);

  const changeBook = (nextBookId: string) => {
    const nextBook = books.find((book) => book.id === nextBookId);
    setBookId(nextBookId);
    setTarget(String(Math.max(1, nextBook?.user_position ?? 1)));
  };

  const submitTask = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setFormError(null);
    if (mode === "web") {
      const value = url.trim();
      if (value.length === 0) {
        setFormError("请输入网页地址");
        return;
      }
      if (await assignWebTask(value)) setUrl("");
      return;
    }
    if (selectedBook === null || !targetIsValid) {
      setFormError("请选择有效的目标段落");
      return;
    }
    await assignBookTask(selectedBook.id, targetParagraph);
  };

  const timeline = data === null ? [] : [...data.schedule];
  const creationResults =
    results?.filter((activity) => activity.type === "creation").slice(0, 12) ??
    null;

  return (
    <Panel title="活动">
      <section className="task-composer">
        <div className="task-composer__header">
          <h3>安排任务</h3>
          <div className="task-mode" role="group" aria-label="任务类型">
            <button
              type="button"
              className={
                mode === "web"
                  ? "task-mode__button task-mode__button--active"
                  : "task-mode__button"
              }
              aria-pressed={mode === "web"}
              onClick={() => setMode("web")}
            >
              网页
            </button>
            <button
              type="button"
              className={
                mode === "book"
                  ? "task-mode__button task-mode__button--active"
                  : "task-mode__button"
              }
              aria-pressed={mode === "book"}
              onClick={() => setMode("book")}
            >
              书籍
            </button>
          </div>
        </div>
        <form className="task-form" onSubmit={(event) => void submitTask(event)}>
          {mode === "web" ? (
            <label className="task-field">
              <span>网页地址</span>
              <input
                type="url"
                value={url}
                placeholder="https://"
                onChange={(event) => setUrl(event.target.value)}
              />
            </label>
          ) : (
            <>
              <div className="task-form__row">
                <label className="task-field">
                  <span>书籍</span>
                  <select
                    value={bookId}
                    disabled={books.length === 0}
                    onChange={(event) => changeBook(event.target.value)}
                  >
                    {books.length === 0 && <option value="">暂无 EPUB</option>}
                    {books.map((book) => (
                      <option key={book.id} value={book.id}>
                        {book.title}
                      </option>
                    ))}
                  </select>
                </label>
                <label className="task-field task-field--target">
                  <span>目标段落</span>
                  <input
                    type="number"
                    min={1}
                    max={selectedBook?.total_paragraphs ?? 1}
                    value={target}
                    onChange={(event) => setTarget(event.target.value)}
                  />
                </label>
              </div>
              {preview.length > 0 && (
                <ol className="task-preview" aria-label="目标段落预览">
                  {preview.map((paragraph) => (
                    <li
                      key={paragraph.id}
                      className={
                        paragraph.index === targetParagraph
                          ? "task-preview__item task-preview__item--target"
                          : "task-preview__item"
                      }
                    >
                      <span>第 {paragraph.index} 段</span>
                      <p>{paragraph.text}</p>
                    </li>
                  ))}
                </ol>
              )}
              {previewError !== null && (
                <p className="error-text">{previewError}</p>
              )}
            </>
          )}
          {(formError ?? taskError ?? booksError) !== null && (
            <p className="error-text">{formError ?? taskError ?? booksError}</p>
          )}
          <button
            type="submit"
            className="task-submit"
            disabled={creatingTask || (mode === "book" && !targetIsValid)}
          >
            {creatingTask ? "安排中…" : "安排阅读"}
          </button>
        </form>
      </section>

      {tasks !== null && tasks.length > 0 && (
        <section className="task-list">
          <h3>任务</h3>
          <ul>
            {tasks.map((task) => (
              <li key={task.id} className="task-list__item">
                <div>
                  <span className="task-list__target">
                    {taskTarget(task, books)}
                  </span>
                  <span className="panel-item__meta">
                    {timeLabel(task.created_at)}
                  </span>
                </div>
                <span className={`task-status task-status--${task.status}`}>
                  {ASSIGNED_TASK_STATUS_LABELS[task.status]}
                </span>
                {task.error !== null && (
                  <p className="error-text">{task.error}</p>
                )}
              </li>
            ))}
          </ul>
        </section>
      )}

      {error !== null && <p className="error-text">{error}</p>}
      {data === null ? (
        "等待核心服务连接…"
      ) : timeline.length === 0 ? (
        <p className="panel-item">今天还没有活动记录</p>
      ) : (
        <ul className="timeline">
          {timeline.map((activity) => {
            const isCurrent = activity.status === "running";
            const result = formatResult(activity);
            return (
              <li
                key={activity.id}
                className={
                  isCurrent
                    ? "timeline__item timeline__item--current"
                    : "timeline__item"
                }
              >
                <span className="timeline__dot" />
                <div className="panel-item">
                  <span className="panel-item__main">
                    {ACTIVITY_TYPE_LABELS[activity.type]}{" "}
                    <span className="panel-badge">
                      {ACTIVITY_STATUS_LABELS[activity.status]}
                    </span>
                    {isCurrent && (
                      <span className="timeline__now">◀ 现在</span>
                    )}
                  </span>
                  <span className="panel-item__meta">
                    {timeLabel(activity.started_at)}
                  </span>
                  {result !== null && (
                    <span className="panel-item__meta">{result}</span>
                  )}
                </div>
              </li>
            );
          })}
        </ul>
      )}
      {creationResults !== null && creationResults.length > 0 && (
        <div className="outputs">
          <h3 className="outputs__title">产出</h3>
          <ul className="outputs__list">
            {creationResults.map((activity) => {
              const title = formatResult(activity);
              if (title === null) return null;
              return (
                <li key={activity.id} className="panel-item">
                  <span className="panel-item__main">{title}</span>
                  <span className="panel-item__meta">
                    {timeLabel(activity.ended_at ?? activity.started_at)}
                  </span>
                </li>
              );
            })}
          </ul>
        </div>
      )}
    </Panel>
  );
}
