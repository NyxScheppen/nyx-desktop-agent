import { useEffect, useState } from "react";
import { useDesireStore } from "../../stores/desireStore";
import { DESIRE_TYPE_LABELS, DESIRE_STATUS_LABELS } from "../../lib/labels";
import Panel from "../layout/Panel";

// 欲望面板（README §5）：REST 快照 + SSE desire_* 触发 refresh。
// 枚举值经 DESIRE_TYPE_LABELS / DESIRE_STATUS_LABELS 转中文。
export default function DesiresPanel() {
  const data = useDesireStore((s) => s.data);
  const error = useDesireStore((s) => s.error);
  const refresh = useDesireStore((s) => s.refresh);
  const [query, setQuery] = useState("");
  const [category, setCategory] = useState("");
  const [period, setPeriod] = useState("all");
  const [page, setPage] = useState(0);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  // 短期欲望只显示「活队列」（pending/active/suppressed）；expired/satisfied 是终态历史，
  // 留在列表里是噪音。后端 /api/desires 仍返回全量供溯源，这里只做展示层过滤，不碰契约。
  const liveShortTerm = (data?.short_term ?? []).filter(
    (d) => d.status !== "expired" && d.status !== "satisfied",
  );
  const entries = [
    ...(data?.long_term ?? []).map((d) => ({ id: d.id, type: d.type, period: "long", title: d.name, body: d.description, meta: `强度 ${d.strength.toFixed(2)} · 进度 ${d.progress.toFixed(2)}` })),
    ...liveShortTerm.map((d) => ({ id: d.id, type: d.type, period: "short", title: d.description, body: "", meta: `强度 ${d.strength.toFixed(2)} · ${DESIRE_STATUS_LABELS[d.status]}` })),
  ].filter((d) => (period === "all" || d.period === period) && (category === "" || d.type === category) && `${d.title} ${d.body}`.toLowerCase().includes(query.trim().toLowerCase()));
  const pages = Math.max(1, Math.ceil(entries.length / 5));
  const currentPage = Math.min(page, pages - 1);

  return (
    <Panel title="欲望">
      {error !== null && <p className="error-text">{error}</p>}
      {data === null ? (
        "等待核心服务连接…"
      ) : (
        <div className="desire-ledger">
          <div className="desire-pressure" aria-label="欲望压力值">
            {data.values.map((value) => (
              <div className="desire-pressure__item" key={value.type}>
                <span>{DESIRE_TYPE_LABELS[value.type]}</span>
                <meter min={0} max={1} value={Math.min(1, Math.max(0, value.value))} />
                <strong>{value.value.toFixed(2)}</strong>
              </div>
            ))}
          </div>
          <div className="panel-toolbar">
            <input className="panel-input" aria-label="搜索欲望" placeholder="搜索欲望…" value={query} onChange={(event) => { setQuery(event.target.value); setPage(0); }} />
            <select className="panel-input" aria-label="欲望类别" value={category} onChange={(event) => { setCategory(event.target.value); setPage(0); }}>
              <option value="">全部类别</option>
              {Object.entries(DESIRE_TYPE_LABELS).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
            </select>
            <div className="panel-tabs" role="group" aria-label="欲望期限">
              {([ ["all", "全部"], ["long", "长期"], ["short", "短期"] ] as const).map(([value, label]) => (
                <button key={value} type="button" className={period === value ? "panel-tab panel-tab--active" : "panel-tab"} aria-pressed={period === value} onClick={() => { setPeriod(value); setPage(0); }}>{label}</button>
              ))}
            </div>
          </div>
          {entries.length === 0 ? <p className="panel-item">暂无符合条件的欲望</p> : (
            <ul className="panel-list">
              {entries.slice(currentPage * 5, currentPage * 5 + 5).map((entry) => (
                <li key={entry.id} className="panel-item">
                  <span className="panel-item__main">〔{DESIRE_TYPE_LABELS[entry.type]}〕{entry.title}</span>
                  {entry.body && <span className="panel-item__body">{entry.body}</span>}
                  <span className="panel-item__meta">{entry.meta}</span>
                </li>
              ))}
            </ul>
          )}
          <div className="ledger-pagination">
            <button type="button" disabled={currentPage === 0} onClick={() => setPage(currentPage - 1)}>上一页</button>
            <span>{currentPage + 1} / {pages}</span>
            <button type="button" disabled={currentPage + 1 >= pages} onClick={() => setPage(currentPage + 1)}>下一页</button>
          </div>
        </div>
      )}
    </Panel>
  );
}
