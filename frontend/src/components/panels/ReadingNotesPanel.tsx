import { useEffect, useRef, useState } from "react";
import {
  addAnnotation,
  deleteAnnotation,
  getAnnotations,
} from "../../api/client";
import { useReadingNotesStore } from "../../stores/readingNotesStore";
import type { Annotation, ReadingNote } from "../../types/api";
import Panel from "../layout/Panel";

// 读书笔记面板：清单（书名 + 内容截断预览 + 日期 + 💬批注数徽标 + 删除）+ 详情（正文 Markdown
// 渲染 + 批注列表 + 增删批注）。笔记清单走 readingNotesStore，选中笔记与其批注用组件本地 state
// （瞬态 UI 不入 store）。
function dateLabel(ts: number): string {
  return new Date(ts * 1000).toLocaleString();
}

// 轻量 Markdown → HTML（标题/加粗/斜体/引用/无序列表/换行），先转义再套标签防注入。
function renderMarkdown(md: string): string {
  return md
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/^### (.+)$/gm, "<h4>$1</h4>")
    .replace(/^## (.+)$/gm, "<h3>$1</h3>")
    .replace(/^# (.+)$/gm, "<h2>$1</h2>")
    .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
    .replace(/\*(.+?)\*/g, "<em>$1</em>")
    .replace(/^&gt; (.+)$/gm, "<blockquote>$1</blockquote>")
    .replace(/^- (.+)$/gm, "<li>$1</li>")
    .replace(/(<li>.*<\/li>\n?)+/g, "<ul>$&</ul>")
    .replace(/\n\n/g, "<br/><br/>")
    .replace(/\n/g, "<br/>");
}

export default function ReadingNotesPanel() {
  const notes = useReadingNotesStore((s) => s.notes);
  const loading = useReadingNotesStore((s) => s.loading);
  const error = useReadingNotesStore((s) => s.error);
  const refresh = useReadingNotesStore((s) => s.refresh);
  const remove = useReadingNotesStore((s) => s.remove);

  // 选中笔记 + 批注（组件本地 state）
  const [selected, setSelected] = useState<ReadingNote | null>(null);
  const [annotations, setAnnotations] = useState<Annotation[]>([]);
  const [annoLoading, setAnnoLoading] = useState(false);
  const [newAnnotation, setNewAnnotation] = useState("");
  const [annoSubmitting, setAnnoSubmitting] = useState(false);
  const [annoError, setAnnoError] = useState<string | null>(null);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const openNote = (note: ReadingNote) => {
    setSelected(note);
    setNewAnnotation("");
    setAnnoError(null);
    void loadAnnotations(note.id);
  };

  // 请求序号：快速切笔记 A→B 时，A 的批注若晚于 B 返回会被序号守卫丢弃，
  // 避免详情视图显示 B 的标题/正文 + A 的批注（陈旧响应竞态）。
  const annoRequest = useRef(0);

  const loadAnnotations = async (noteId: string) => {
    const req = ++annoRequest.current;
    setAnnoLoading(true);
    try {
      const result = await getAnnotations(noteId);
      if (req !== annoRequest.current) return;
      setAnnotations(result);
    } catch {
      if (req !== annoRequest.current) return;
      setAnnotations([]);
    } finally {
      if (req === annoRequest.current) setAnnoLoading(false);
    }
  };

  const onAddAnnotation = async () => {
    const content = newAnnotation.trim();
    if (content.length === 0 || selected === null) return;
    setAnnoSubmitting(true);
    setAnnoError(null);
    try {
      await addAnnotation(selected.id, content);
      setNewAnnotation("");
      await loadAnnotations(selected.id);
      await refresh();   // 列表 annotation_count 徽标跟随刷新
    } catch (err) {
      setAnnoError(err instanceof Error ? err.message : String(err));
    } finally {
      setAnnoSubmitting(false);
    }
  };

  const onDeleteAnnotation = async (annotationId: string) => {
    try {
      await deleteAnnotation(annotationId);
      setAnnotations((prev) => prev.filter((a) => a.id !== annotationId));
      await refresh();   // 列表 annotation_count 徽标跟随刷新
    } catch {
      /* 删除失败不打断阅读 */
    }
  };

  const onDeleteNote = async (noteId: string) => {
    if (!window.confirm("确定要删除这篇读书笔记吗？关联批注也会一并删除。")) return;
    await remove(noteId);
    if (selected?.id === noteId) {
      annoRequest.current++;
      setSelected(null);
      setAnnotations([]);
    }
  };

  // 详情视图
  if (selected !== null) {
    return (
      <Panel title="读书笔记">
        <div className="panel-toolbar">
          <button
            type="button"
            className="panel-refresh"
            onClick={() => {
              annoRequest.current++;
              setSelected(null);
              setAnnotations([]);
            }}
          >
            ← 返回
          </button>
          <button
            type="button"
            className="panel-refresh"
            onClick={() => void onDeleteNote(selected.id)}
          >
            🗑 删除
          </button>
        </div>
        <span className="panel-item__main">《{selected.book}》笔记</span>
        <span className="panel-item__meta">{dateLabel(selected.created_at)}</span>
        <div
          className="panel-item__body"
          dangerouslySetInnerHTML={{ __html: renderMarkdown(selected.content) }}
        />

        <div className="panel-section">
          <h3 className="panel-section-title">批注（{annotations.length}）</h3>
          {annoLoading ? (
            <p className="panel-item">加载批注中……</p>
          ) : annotations.length === 0 ? (
            <p className="panel-item">暂无批注</p>
          ) : (
            <ul className="panel-list">
              {annotations.map((a) => (
                <li key={a.id} className="panel-item">
                  <span className="panel-item__main">
                    {a.author === "nyx" ? "尼克斯" : "你"}
                  </span>
                  <span className="panel-item__meta">{dateLabel(a.created_at)}</span>
                  <span className="panel-item__body">{a.content}</span>
                  {a.author === "user" && (
                    <button
                      type="button"
                      className="panel-refresh"
                      onClick={() => void onDeleteAnnotation(a.id)}
                    >
                      删除
                    </button>
                  )}
                </li>
              ))}
            </ul>
          )}
          {annoError !== null && <p className="error-text">{annoError}</p>}
          <textarea
            className="panel-input"
            placeholder="添加你的批注……"
            value={newAnnotation}
            onChange={(e) => setNewAnnotation(e.target.value)}
            rows={3}
          />
          <button
            type="button"
            className="panel-refresh"
            disabled={newAnnotation.trim().length === 0 || annoSubmitting}
            onClick={() => void onAddAnnotation()}
          >
            {annoSubmitting ? "提交中……" : "添加批注"}
          </button>
        </div>
      </Panel>
    );
  }

  return (
    <Panel title="读书笔记">
      {error !== null && <p className="error-text">{error}</p>}
      {notes === null ? (
        loading ? (
          "加载中……"
        ) : (
          "等待核心服务连接…"
        )
      ) : notes.length === 0 ? (
        <p className="panel-item">
          还没有读书笔记。尼克斯读完一本书后会自动生成。
        </p>
      ) : (
        <ul className="panel-list">
          {notes.map((note) => (
            <li
              key={note.id}
              className="panel-item"
              onClick={() => openNote(note)}
              style={{ cursor: "pointer" }}
            >
              <span className="panel-item__main">
                《{note.book}》
                {note.annotation_count > 0 && (
                  <span className="panel-badge">💬{note.annotation_count}</span>
                )}
              </span>
              <span className="panel-item__meta">{dateLabel(note.created_at)}</span>
              <span className="panel-item__body">
                {note.content.slice(0, 80)}
                {note.content.length > 80 ? "…" : ""}
              </span>
              <button
                type="button"
                className="panel-refresh"
                onClick={(e) => {
                  e.stopPropagation();
                  void onDeleteNote(note.id);
                }}
              >
                🗑 删除
              </button>
            </li>
          ))}
        </ul>
      )}
    </Panel>
  );
}
