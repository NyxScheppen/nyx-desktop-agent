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
        {LABELS[event.type] ?? event.type} · {new Date(event.timestamp * 1000).toLocaleString()}
      </span>
    </li>
  ))}</ul>;
}

export default function ExpressionHistoryPanel() {
  const data = useExpressionHistoryStore((state) => state.data);
  const error = useExpressionHistoryStore((state) => state.error);
  const refresh = useExpressionHistoryStore((state) => state.refresh);
  const [tab, setTab] = useState<"all" | "mutter" | "initiate_chat">("all");
  useEffect(() => { void refresh(); }, [refresh]);
  return (
    <Panel title="表达历史">
      <div className="panel-tabs" role="tablist" aria-label="表达历史类型">
        {(["all", "mutter", "initiate_chat"] as const).map((key) => (
          <button key={key} type="button" role="tab" aria-selected={tab === key}
            className={tab === key ? "panel-tab panel-tab--active" : "panel-tab"}
            onClick={() => setTab(key)}>
            {key === "all" ? "全部历史" : key === "mutter" ? "碎碎念" : "主动搭话"}
          </button>
        ))}
      </div>
      {error !== null && <p className="error-text">{error}</p>}
      {data === null ? "等待核心服务连接…" : <EventList events={data[tab]} />}
    </Panel>
  );
}
