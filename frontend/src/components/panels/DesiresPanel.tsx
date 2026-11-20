import { formatPolicyText } from "../../lib/policy";
import desirePolicy from "../../../../nyx/settings/desire.json";
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
    ...(data?.long_term ?? []).map((d) => ({ id: d.id, type: d.type, period: "long", title: d.name, body: d.description, meta: formatPolicyText(desirePolicy.templates.DesiresPanel.meta, { p0: d.strength.toFixed(2), p1: d.progress.toFixed(2) }) })),
    ...liveShortTerm.map((d) => ({ id: d.id, type: d.type, period: "short", title: d.description, body: "", meta: formatPolicyText(desirePolicy.templates.DesiresPanel.meta_2, { p0: d.strength.toFixed(2), p1: DESIRE_STATUS_LABELS[d.status] }) })),
  ].filter((d) => (period === "all" || d.period === period) && (category === "" || d.type === category) && `${d.title} ${d.body}`.toLowerCase().includes(query.trim().toLowerCase()));
  const pages = Math.max(1, Math.ceil(entries.length / 5));
  const currentPage = Math.min(page, pages - 1);

  return (
    <Panel title={desirePolicy.copy.DesiresPanel.title}>
      {error !== null && <p className="error-text">{error}</p>}
      {data === null ? (
        desirePolicy.copy.DesiresPanel.text
      ) : (
        <div className="desire-ledger">
          <h3>{desirePolicy.copy.DesiresPanel.h3}</h3>
          <p className="panel-item__meta">{desirePolicy.copy.DesiresPanel.p}</p>
          <div className="desire-pressure" aria-label={desirePolicy.copy.DesiresPanel.aria_label}>
            {data.values.map((value) => (
              <div className="desire-pressure__item" key={value.type}>
                <span>{DESIRE_TYPE_LABELS[value.type]}</span>
                <meter min={0} max={1} value={Math.min(1, Math.max(0, value.value))} />
                <strong>{value.value.toFixed(2)}</strong>
                <small>{desirePolicy.copy.DesiresPanel.small}{value.suppression_threshold.toFixed(2)}</small>
              </div>
            ))}
          </div>
          <h3>{desirePolicy.copy.DesiresPanel.h3_2}</h3>
          <div aria-label={desirePolicy.copy.DesiresPanel.aria_label_2}>
            {Object.entries(DESIRE_TYPE_LABELS).map(([type, label]) => {
              const ready = liveShortTerm.filter((d) => d.type === type && d.status === "pending").length;
              const active = liveShortTerm.filter((d) => d.type === type && d.status === "active").length;
              return <p key={type}>{label}：{ready > 0 || active > 0 ? formatPolicyText(desirePolicy.templates.DesiresPanel.text, { p0: ready, p1: active }) : desirePolicy.copy.DesiresPanel.text_2}</p>;
            })}
          </div>
          <p className="panel-item__meta">{desirePolicy.copy.DesiresPanel.p_2}</p>
          <h3>{desirePolicy.copy.DesiresPanel.h3_3}</h3>
          <div className="panel-toolbar">
            <input className="panel-input" aria-label={desirePolicy.copy.DesiresPanel.aria_label_3} placeholder={desirePolicy.copy.DesiresPanel.placeholder} value={query} onChange={(event) => { setQuery(event.target.value); setPage(0); }} />
            <select className="panel-input" aria-label={desirePolicy.copy.DesiresPanel.aria_label_4} value={category} onChange={(event) => { setCategory(event.target.value); setPage(0); }}>
              <option value="">{desirePolicy.copy.DesiresPanel.option}</option>
              {Object.entries(DESIRE_TYPE_LABELS).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
            </select>
            <div className="panel-tabs" role="group" aria-label={desirePolicy.copy.DesiresPanel.aria_label_5}>
              {([ ["all", desirePolicy.copy.DesiresPanel.text_3], ["long", desirePolicy.copy.DesiresPanel.text_4], ["short", desirePolicy.copy.DesiresPanel.text_5] ] as const).map(([value, label]) => (
                <button key={value} type="button" className={period === value ? "panel-tab panel-tab--active" : "panel-tab"} aria-pressed={period === value} onClick={() => { setPeriod(value); setPage(0); }}>{label}</button>
              ))}
            </div>
          </div>
          {entries.length === 0 ? <p className="panel-item">{desirePolicy.copy.DesiresPanel.p_3}</p> : (
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
            <button type="button" disabled={currentPage === 0} onClick={() => setPage(currentPage - 1)}>{desirePolicy.copy.DesiresPanel.button}</button>
            <span>{currentPage + 1} / {pages}</span>
            <button type="button" disabled={currentPage + 1 >= pages} onClick={() => setPage(currentPage + 1)}>{desirePolicy.copy.DesiresPanel.button_2}</button>
          </div>
        </div>
      )}
    </Panel>
  );
}
