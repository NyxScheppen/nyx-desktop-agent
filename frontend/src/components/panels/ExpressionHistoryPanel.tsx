import { formatDateTime } from "../../lib/time";
import { useEffect, useState } from "react";
import { useExpressionHistoryStore } from "../../stores/expressionHistoryStore";
import Panel from "../layout/Panel";
import type { BackendEvent } from "../../types/api";

const LABELS: Record<string, string> = {
  user_message: "用户消息", speak: "回复", ask: "提问", think: "思考",
  reading_question: "陪读提问", reading_association: "陪读联想",
  mutter: "碎碎念", reading_mutter: "陪读碎碎念", initiate_chat: "主动搭话",
};

function content(event: BackendEvent): string {
  const value = event.content.content ?? event.content.message ?? event.content.snippet;
  return typeof value === "string" ? value : "（无文本内容）";
}

function EventList({ events }: { events: BackendEvent[] }) {
  if (events.length === 0) return <p className="panel-item">暂无记录</p>;
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
  const [page, setPage] = useState(0);
  const events = (data?.[tab] ?? []).filter((event) =>
    (!conversationOnly || ["user_message", "speak", "ask", "reading_question", "initiate_chat"].includes(event.type)) &&
    content(event).toLowerCase().includes(query.trim().toLowerCase())
  );
  const pages = Math.max(1, Math.ceil(events.length / 5));
  const currentPage = Math.min(page, pages - 1);
  useEffect(() => { void refresh(); }, [refresh]);
  return (
    <Panel title="表达历史">
      <div className="panel-tabs" role="tablist" aria-label="表达历史类型">
        {(["all", "mutter", "initiate_chat"] as const).map((key) => (
          <button key={key} type="button" role="tab" aria-selected={tab === key}
            className={tab === key ? "panel-tab panel-tab--active" : "panel-tab"}
            onClick={() => { setTab(key); setPage(0); }}>
            {key === "all" ? "全部历史" : key === "mutter" ? "碎碎念" : "主动搭话"}
          </button>
        ))}
      </div>
      <div className="panel-toolbar">
        <input className="panel-input" aria-label="搜索表达历史" placeholder="搜索历史…" value={query} onChange={(event) => { setQuery(event.target.value); setPage(0); }} />
        <label className="history-conversation-filter"><input type="checkbox" checked={conversationOnly} onChange={(event) => { setConversationOnly(event.target.checked); setPage(0); }} />仅对话</label>
      </div>
      {error !== null && <p className="error-text">{error}</p>}
      {data === null ? "等待核心服务连接…" : <EventList events={events.slice(currentPage * 5, currentPage * 5 + 5)} />}
      {data !== null && events.length > 0 && <div className="ledger-pagination">
        <button type="button" disabled={currentPage === 0} onClick={() => setPage(currentPage - 1)}>上一页</button>
        <span>{currentPage + 1} / {pages}</span>
        <button type="button" disabled={currentPage + 1 >= pages} onClick={() => setPage(currentPage + 1)}>下一页</button>
      </div>}
    </Panel>
  );
}
