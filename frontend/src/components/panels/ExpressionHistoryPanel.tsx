import expressionPolicy from "../../../../nyx/settings/expression.json";
import frontendPolicy from "../../../../nyx/settings/frontend.json";
import { formatDateTime } from "../../lib/time";
import { useEffect, useState } from "react";
import { useExpressionHistoryStore } from "../../stores/expressionHistoryStore";
import Panel from "../layout/Panel";
import type { BackendEvent } from "../../types/api";
import FlowPages from "../layout/FlowPages";

const LABELS: Record<string, string> = frontendPolicy.frontend.ExpressionHistoryPanel.LABELS as Record<string, string>;

function content(event: BackendEvent): string {
  const value = event.content.content ?? event.content.message ?? event.content.snippet;
  return typeof value === "string" ? value : expressionPolicy.copy.ExpressionHistoryPanel.text;
}

function EventList({ events }: { events: BackendEvent[] }) {
  if (events.length === 0) return <p className="panel-item">{expressionPolicy.copy.ExpressionHistoryPanel.p}</p>;
  return <ul className="panel-list">{events.map((event) => (
    <li className="panel-item" key={event.id}>
      <span className="panel-item__main">{content(event)}</span>
      <span className="panel-item__meta">
        {LABELS[event.type] ?? event.type} · {formatDateTime(event.timestamp)}
      </span>
    </li>
  ))}</ul>;
}

export default function ExpressionHistoryPanel() {
  const data = useExpressionHistoryStore((state) => state.data);
  const error = useExpressionHistoryStore((state) => state.error);
  const refresh = useExpressionHistoryStore((state) => state.refresh);
  const [tab, setTab] = useState<"all" | "mutter" | "initiate_chat">("all");
  const [conversationOnly, setConversationOnly] = useState(false);
  const [query, setQuery] = useState("");
  const events = (data?.[tab] ?? []).filter((event) =>
    (!conversationOnly || ["user_message", "speak", "ask", "reading_question", "initiate_chat"].includes(event.type)) &&
    content(event).toLowerCase().includes(query.trim().toLowerCase())
  );
  useEffect(() => { void refresh(); }, [refresh]);
  return (
    <Panel title={expressionPolicy.copy.ExpressionHistoryPanel.title} className="paged-panel history-panel">
      <div className="panel-tabs" role="tablist" aria-label={expressionPolicy.copy.ExpressionHistoryPanel.aria_label}>
        {(["all", "mutter", "initiate_chat"] as const).map((key) => (
          <button key={key} type="button" role="tab" aria-selected={tab === key}
            className={tab === key ? "panel-tab panel-tab--active" : "panel-tab"}
            onClick={() => setTab(key)}>
            {key === "all" ? expressionPolicy.copy.ExpressionHistoryPanel.text_2 : key === "mutter" ? expressionPolicy.copy.ExpressionHistoryPanel.text_3 : expressionPolicy.copy.ExpressionHistoryPanel.text_4}
          </button>
        ))}
      </div>
      <div className="panel-toolbar">
        <input className="panel-input" aria-label={expressionPolicy.copy.ExpressionHistoryPanel.aria_label_2} placeholder={expressionPolicy.copy.ExpressionHistoryPanel.placeholder} value={query} onChange={(event) => setQuery(event.target.value)} />
        <label className="history-conversation-filter"><input type="checkbox" checked={conversationOnly} onChange={(event) => setConversationOnly(event.target.checked)} />{expressionPolicy.copy.ExpressionHistoryPanel.label}</label>
      </div>
      {error !== null && <p className="error-text">{error}</p>}
      {data === null ? expressionPolicy.copy.ExpressionHistoryPanel.text_5 : <FlowPages key={`${tab}:${conversationOnly}:${query}`}><EventList events={events} /></FlowPages>}
    </Panel>
  );
}
