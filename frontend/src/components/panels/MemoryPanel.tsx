import { useEffect, useState } from "react";
import { exportMemories, searchMemories } from "../../api/client";
import { useMemoryStore } from "../../stores/memoryStore";
import { MEMORY_TYPE_LABELS } from "../../lib/labels";
import type { Memory, MemoryType } from "../../types/api";
import Panel from "../layout/Panel";

// 记忆浏览器面板：搜索（后端语义检索）+ tag/类型筛选 + 排序 + 展开完整内容。
// 搜索走后端 /api/memories/search（三层检索）；tag/类型筛选、排序对已拉取列表本地处理。
type SortKey = "default" | "time" | "freshness" | "recall";

function bySort(key: SortKey): (a: Memory, b: Memory) => number {
  switch (key) {
    case "time":
      return (a, b) => b.created_at - a.created_at;
    case "freshness":
      return (a, b) => b.freshness - a.freshness;
    case "recall":
      return (a, b) => b.recall_count - a.recall_count;
    default:
      return () => 0;
  }
}

function dateLabel(ts: number): string {
  const d = new Date(ts * 1000);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

function download(text: string, filename: string): void {
  const blob = new Blob([text], { type: "text/plain;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

export default function MemoryPanel() {
  const data = useMemoryStore((s) => s.data);
  const error = useMemoryStore((s) => s.error);
  const refresh = useMemoryStore((s) => s.refresh);
  const [exportError, setExportError] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<Memory[] | null>(null);
  const [searchError, setSearchError] = useState<string | null>(null);
  const [tagFilter, setTagFilter] = useState("");
  const [typeFilter, setTypeFilter] = useState<MemoryType | "">("");
  const [sortBy, setSortBy] = useState<SortKey>("default");
  const [expandedId, setExpandedId] = useState<string | null>(null);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  // 搜索防抖 300ms：空词回退全量列表；命中走后端语义检索。
  useEffect(() => {
    const q = query.trim();
    if (q === "") {
      setResults(null);
      setSearchError(null);
      return;
    }
    const timer = setTimeout(() => {
      searchMemories(q)
        .then((r) => {
          setResults(r);
          setSearchError(null);
        })
        .catch((err) => {
          setResults([]);
          setSearchError(err instanceof Error ? err.message : String(err));
        });
    }, 300);
    return () => clearTimeout(timer);
  }, [query]);

  const onExport = (fmt: "json" | "md") => async () => {
    setExportError(null);
    try {
      const text = await exportMemories(fmt);
      download(text, fmt === "json" ? "memories.json" : "memories.md");
    } catch (err) {
      setExportError(err instanceof Error ? err.message : String(err));
    }
  };

  const source = results ?? data;
  const tags = [...new Set((data ?? []).map((m) => m.tag))].sort();
  const visible = (source ?? [])
    .filter((m) => tagFilter === "" || m.tag === tagFilter)
    .filter((m) => typeFilter === "" || m.type === typeFilter);
  const sorted = [...visible].sort(bySort(sortBy));

  return (
    <Panel title="记忆">
      <input
        type="search"
        className="panel-input"
        placeholder="搜索记忆…"
        value={query}
        onChange={(e) => setQuery(e.target.value)}
      />
      <div className="panel-toolbar">
        <select
          className="panel-refresh"
          aria-label="标签筛选"
          value={tagFilter}
          onChange={(e) => setTagFilter(e.target.value)}
        >
          <option value="">全部标签</option>
          {tags.map((t) => (
            <option key={t} value={t}>
              {t}
            </option>
          ))}
        </select>
        <select
          className="panel-refresh"
          aria-label="类型筛选"
          value={typeFilter}
          onChange={(e) => setTypeFilter(e.target.value as MemoryType | "")}
        >
          <option value="">全部类型</option>
          <option value="short_term">短期</option>
          <option value="long_term">长期</option>
        </select>
        <select
          className="panel-refresh"
          aria-label="排序"
          value={sortBy}
          onChange={(e) => setSortBy(e.target.value as SortKey)}
        >
          <option value="default">默认排序</option>
          <option value="time">按时间</option>
          <option value="freshness">按新鲜度</option>
          <option value="recall">按召回次数</option>
        </select>
        <button type="button" className="panel-refresh" onClick={onExport("json")}>
          导出 JSON
        </button>
        <button type="button" className="panel-refresh" onClick={onExport("md")}>
          导出 Markdown
        </button>
      </div>
      {error !== null && <p className="error-text">{error}</p>}
      {exportError !== null && <p className="error-text">{exportError}</p>}
      {searchError !== null && <p className="error-text">{searchError}</p>}
      {source === null ? (
        "等待核心服务连接…"
      ) : sorted.length === 0 ? (
        <p className="panel-item">
          {query.trim() !== "" ? "无匹配记忆" : "暂无记忆"}
        </p>
      ) : (
        <ul className="panel-list">
          {sorted.map((m) => (
            <li
              key={m.id}
              className="panel-item"
              onClick={() => setExpandedId(expandedId === m.id ? null : m.id)}
            >
              <span className="panel-item__main">{m.summary || m.content}</span>
              <span className="panel-item__meta">
                {m.tag} · {MEMORY_TYPE_LABELS[m.type]} · 召回×{m.recall_count} ·{" "}
                freshness={m.freshness.toFixed(2)} · {dateLabel(m.created_at)}
              </span>
              {expandedId === m.id && (
                <span className="panel-item__body">{m.content}</span>
              )}
            </li>
          ))}
        </ul>
      )}
    </Panel>
  );
}
