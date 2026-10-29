import { useEffect, useMemo, useRef, useState, type FormEvent, type PointerEvent } from "react";
import { captureScope, isScopeCurrent } from "../../api/characterScope";
import { getActivityResults, getBookParagraphs } from "../../api/client";
import { formatResult } from "../../lib/activityResult";
import {
  ACTIVITY_STATUS_LABELS,
  ACTIVITY_TYPE_LABELS,
  ASSIGNED_TASK_STATUS_LABELS,
} from "../../lib/labels";
import { useActivityStore } from "../../stores/activityStore";
import { useReaderStore } from "../../stores/readerStore";
import type { Activity, AssignedTask, BookListItem, Paragraph } from "../../types/api";
import Panel from "../layout/Panel";

function timeLabel(ts: number): string {
  return new Date(ts * 1000).toLocaleTimeString();
}

function taskTarget(task: AssignedTask, books: BookListItem[]): string {
  if (task.type === "web") return task.url ?? "网页";
  if (task.type === "browser_survey") {
    return `网页问卷 · ${task.url ?? "未知地址"}`;
  }
  if (task.type === "creation") return "创作任务";
  const book = books.find((item) => item.id === task.book_id);
  const name = book?.title ?? task.book_id ?? "已删除书籍";
  return task.target_paragraph === null
    ? name
    : `${name} · 第 ${task.target_paragraph} 段`;
}

export default function ActivityPanel({ view = "activity" }: { view?: "activity" | "tasks" | "task-compose" | "task-list" }) {
  const data = useActivityStore((state) => state.data);
  const tasks = useActivityStore((state) => state.tasks);
  const error = useActivityStore((state) => state.error);
  const taskError = useActivityStore((state) => state.taskError);
  const creatingTask = useActivityStore((state) => state.creatingTask);
  const refresh = useActivityStore((state) => state.refresh);
  const assignWebTask = useActivityStore((state) => state.createWebTask);
  const assignBrowserSurveyTask = useActivityStore((state) => state.createBrowserSurveyTask);
  const assignBookTask = useActivityStore((state) => state.createBookTask);
  const books = useReaderStore((state) => state.books);
  const booksError = useReaderStore((state) => state.booksError);
  const loadBooks = useReaderStore((state) => state.loadBooks);

  const [taskPageView, setTaskPageView] = useState<"records" | "compose">(view === "task-compose" ? "compose" : "records");
  const [webUrl, setWebUrl] = useState("");
  const [surveyUrl, setSurveyUrl] = useState("");
  const [bookId, setBookId] = useState("");
  const [target, setTarget] = useState("1");
  const [preview, setPreview] = useState<Paragraph[]>([]);
  const [previewError, setPreviewError] = useState<string | null>(null);
  const [formError, setFormError] = useState<string | null>(null);
  const [selectedTaskId, setSelectedTaskId] = useState<string | null>(null);
  const [taskQuery, setTaskQuery] = useState("");
  const [taskPage, setTaskPage] = useState(0);
  const [history, setHistory] = useState<Activity[] | null>(null);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [showHistory, setShowHistory] = useState(false);
  const timelineRef = useRef<HTMLUListElement>(null);
  const dragOrigin = useRef<{ x: number; scroll: number } | null>(null);

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
    if (selectedBook === null || !targetIsValid) {
      setPreview([]);
      setPreviewError(null);
      return;
    }
    let active = true;
    const scope = captureScope();
    setPreviewError(null);
    void getBookParagraphs(
      selectedBook.id,
      Math.max(1, targetParagraph - 1),
      Math.min(selectedBook.total_paragraphs, targetParagraph + 1),
    )
      .then((paragraphs) => {
        if (active && isScopeCurrent(scope)) setPreview(paragraphs);
      })
      .catch((reason: unknown) => {
        if (!active || !isScopeCurrent(scope)) return;
        setPreview([]);
        setPreviewError(reason instanceof Error ? reason.message : String(reason));
      });
    return () => {
      active = false;
    };
  }, [selectedBook, targetIsValid, targetParagraph]);

  const changeBook = (nextBookId: string) => {
    const nextBook = books.find((book) => book.id === nextBookId);
    setBookId(nextBookId);
    setTarget(String(Math.max(1, nextBook?.user_position ?? 1)));
  };

  const submitTask = async (event: FormEvent<HTMLFormElement>, taskMode: "web" | "book" | "browser_survey") => {
    event.preventDefault();
    const scope = captureScope();
    setFormError(null);
    if (taskMode === "web" || taskMode === "browser_survey") {
      const value = (taskMode === "web" ? webUrl : surveyUrl).trim();
      if (value.length === 0) {
        setFormError("请输入网页地址");
        return;
      }
      const created = taskMode === "web"
        ? await assignWebTask(value)
        : await assignBrowserSurveyTask(value);
      if (created && isScopeCurrent(scope)) {
        if (taskMode === "web") setWebUrl("");
        else setSurveyUrl("");
      }
      return;
    }
    if (selectedBook === null || !targetIsValid) {
      setFormError("请选择有效的目标段落");
      return;
    }
    await assignBookTask(selectedBook.id, targetParagraph);
  };

  const timeline = data === null ? [] : [...data.schedule];
  const selectedTask = tasks?.find((task) => task.id === selectedTaskId);
  const filteredTasks = (tasks ?? []).filter((task) => taskTarget(task, books).toLowerCase().includes(taskQuery.trim().toLowerCase()));
  const taskPages = Math.max(1, Math.ceil(filteredTasks.length / 5));
  const currentTaskPage = Math.min(taskPage, taskPages - 1);
  const showTaskPage = view === "tasks" || view === "task-compose" || view === "task-list";
  const showComposer = showTaskPage && taskPageView === "compose";
  const showTaskList = showTaskPage && taskPageView === "records";
  const dragTimeline = (event: PointerEvent<HTMLUListElement>) => {
    if (dragOrigin.current === null || timelineRef.current === null) return;
    timelineRef.current.scrollLeft = dragOrigin.current.scroll + dragOrigin.current.x - event.clientX;
  };
  const loadHistory = async () => {
    if (historyLoading) return;
    setHistoryLoading(true);
    try {
      setHistory(await getActivityResults({ limit: 100 }));
    } catch (reason) {
      setFormError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setHistoryLoading(false);
    }
  };

  return (
    <Panel title={showTaskPage ? "任务" : "活动"} className={showTaskPage ? "task-page" : "activity-page"}>
      {showTaskPage && <div className="task-page-tabs" role="tablist" aria-label="任务视图">
        <button type="button" role="tab" aria-selected={showTaskList} className={showTaskList ? "panel-tab panel-tab--active" : "panel-tab"} onClick={() => setTaskPageView("records")}>任务记录</button>
        <button type="button" role="tab" aria-selected={showComposer} className={showComposer ? "panel-tab panel-tab--active" : "panel-tab"} onClick={() => setTaskPageView("compose")}>安排任务</button>
      </div>}

      {showComposer && <section className="task-composer">
        <div className="task-composer__header"><h3>安排任务</h3></div>
        <div className="task-form-grid">
          <form className="task-form" onSubmit={(event) => void submitTask(event, "web")}>
            <h4>网页</h4>
            <label className="task-field"><span>网页地址</span><input type="url" value={webUrl} placeholder="https://" onChange={(event) => setWebUrl(event.target.value)} /></label>
            <button type="submit" className="task-submit" disabled={creatingTask}>{creatingTask ? "安排中…" : "安排网页"}</button>
          </form>
          <form className="task-form" onSubmit={(event) => void submitTask(event, "book")}>
            <h4>书籍</h4>
            <div className="task-form__row">
              <label className="task-field"><span>书籍</span><select value={bookId} disabled={books.length === 0} onChange={(event) => changeBook(event.target.value)}>{books.length === 0 && <option value="">暂无 EPUB</option>}{books.map((book) => <option key={book.id} value={book.id}>{book.title}</option>)}</select></label>
              <label className="task-field task-field--target"><span>目标段落</span><input type="number" min={1} max={selectedBook?.total_paragraphs ?? 1} value={target} onChange={(event) => setTarget(event.target.value)} /></label>
            </div>
            {preview.length > 0 && <ol className="task-preview" aria-label="目标段落预览">{preview.map((paragraph) => <li key={paragraph.id} className={paragraph.index === targetParagraph ? "task-preview__item task-preview__item--target" : "task-preview__item"}><span>第 {paragraph.index} 段</span><p>{paragraph.text}</p></li>)}</ol>}
            {previewError !== null && <p className="error-text">{previewError}</p>}
            <button type="submit" className="task-submit" disabled={creatingTask || !targetIsValid}>{creatingTask ? "安排中…" : "安排阅读"}</button>
          </form>
          <form className="task-form" onSubmit={(event) => void submitTask(event, "browser_survey")}>
            <h4>网页问卷</h4>
            <label className="task-field"><span>网页地址</span><input type="url" value={surveyUrl} placeholder="https://" onChange={(event) => setSurveyUrl(event.target.value)} /></label>
            <button type="submit" className="task-submit" disabled={creatingTask}>{creatingTask ? "安排中…" : "安排问卷"}</button>
          </form>
        </div>
        {(formError ?? taskError ?? booksError) !== null && <p className="error-text">{formError ?? taskError ?? booksError}</p>}
      </section>}

      {showTaskList && <>
        <section className="task-list">
          <h3>任务</h3>
          <input className="panel-input" type="search" aria-label="搜索任务" placeholder="搜索任务…" value={taskQuery} onChange={(event) => { setTaskQuery(event.target.value); setTaskPage(0); }} />
          {tasks === null && <p>正在加载任务…</p>}
          {tasks !== null && tasks.length === 0 && <p>还没有安排任务</p>}
          <ul>
            {filteredTasks.slice(currentTaskPage * 5, currentTaskPage * 5 + 5).map((task) => (
              <li key={task.id} className="task-list__item">
                <button type="button" className="task-list__select" aria-label={`查看任务 ${taskTarget(task, books)}`} onClick={() => setSelectedTaskId(task.id)}>
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
                </button>
                {task.error !== null && (
                  <p className="error-text">{task.error}</p>
                )}
              </li>
            ))}
          </ul>
          {tasks !== null && tasks.length > 0 && filteredTasks.length === 0 && <p>没有匹配的任务</p>}
          {filteredTasks.length > 0 && <div className="ledger-pagination">
            <button type="button" disabled={currentTaskPage === 0} onClick={() => setTaskPage(currentTaskPage - 1)}>上一页</button>
            <span>{currentTaskPage + 1} / {taskPages}</span>
            <button type="button" disabled={currentTaskPage + 1 >= taskPages} onClick={() => setTaskPage(currentTaskPage + 1)}>下一页</button>
          </div>}
        </section>
      {selectedTask !== undefined && <aside className="task-detail" aria-label="任务详情">
        <button type="button" className="task-detail__close" onClick={() => setSelectedTaskId(null)} aria-label="关闭任务详情">×</button>
        <h3>{taskTarget(selectedTask, books)}</h3>
        <p>{ASSIGNED_TASK_STATUS_LABELS[selectedTask.status]}</p>
        <p>创建：{new Date(selectedTask.created_at * 1000).toLocaleString()}</p>
        {selectedTask.error !== null && <p className="error-text">{selectedTask.error}</p>}
        {typeof (selectedTask.checkpoint.result as Record<string, unknown> | undefined)?.summary === "string" && <p>{String((selectedTask.checkpoint.result as Record<string, unknown>).summary)}</p>}
      </aside>}
      </>}

      {view === "activity" && <>
      <div className="activity-day">
        <div className="activity-day__date"><strong>今天</strong><time>{new Date().toLocaleDateString()}</time></div>
        <button
          type="button"
          className="panel-refresh activity-history-toggle"
          aria-expanded={showHistory}
          onClick={() => {
            if (showHistory) {
              setShowHistory(false);
            } else {
              setShowHistory(true);
              void loadHistory();
            }
          }}
          disabled={historyLoading}
        >
          {historyLoading ? "正在读取历史…" : showHistory ? "收起历史活动" : "查看历史活动"}
        </button>
      </div>
      {error !== null && <p className="error-text">{error}</p>}
      {formError !== null && <p className="error-text">{formError}</p>}
      {data === null ? (
        "等待核心服务连接…"
      ) : timeline.length === 0 ? (
        <p className="panel-item">今天还没有活动记录</p>
      ) : (
        <ul className="timeline" ref={timelineRef} tabIndex={0} aria-label="今天的活动时间线"
          onKeyDown={(event) => { if (event.key === "ArrowRight" || event.key === "ArrowLeft") { event.preventDefault(); event.currentTarget.scrollBy({ left: event.key === "ArrowRight" ? 180 : -180, behavior: "smooth" }); } }}
          onPointerDown={(event) => { if (event.button !== 0) return; dragOrigin.current = { x: event.clientX, scroll: event.currentTarget.scrollLeft }; event.currentTarget.setPointerCapture(event.pointerId); }}
          onPointerMove={dragTimeline}
          onPointerUp={() => { dragOrigin.current = null; }}
          onPointerCancel={() => { dragOrigin.current = null; }}
          onLostPointerCapture={() => { dragOrigin.current = null; }}>
          {timeline.map((activity, index) => {
            const isCurrent = activity.status === "running";
            const result = formatResult(activity);
            return (
              <li
                key={activity.id}
                className={`timeline__item${isCurrent ? " timeline__item--current" : ""}${Math.floor(index / 3) % 2 === 1 ? " timeline__item--up" : ""}${index % 3 === 2 ? " timeline__item--column-end" : ""}${index === timeline.length - 1 ? " timeline__item--last" : ""}`}
                style={{ gridColumn: Math.floor(index / 3) + 1, gridRow: Math.floor(index / 3) % 2 === 0 ? index % 3 + 1 : 3 - index % 3 }}
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
                    <span className="panel-item__meta timeline__result">{result}</span>
                  )}
                </div>
              </li>
            );
          })}
        </ul>
      )}
      {showHistory && history !== null && <section className="activity-history" aria-label="历史活动记录">
        <h3>历史活动记录</h3>
        {history.length === 0 ? <p className="panel-item">暂无历史活动</p> : <ul className="panel-list">
          {history.map((activity) => <li key={activity.id} className="panel-item">
            <span className="panel-item__main">{ACTIVITY_TYPE_LABELS[activity.type]} <span className="panel-badge">{ACTIVITY_STATUS_LABELS[activity.status]}</span></span>
            <span className="panel-item__meta">{new Date(activity.started_at * 1000).toLocaleString()}</span>
            {formatResult(activity) !== null && <span className="panel-item__meta">{formatResult(activity)}</span>}
          </li>)}
        </ul>}
      </section>}
      </>}
    </Panel>
  );
}
