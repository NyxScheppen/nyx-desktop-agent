import { useEffect } from "react";
import { getEventsLog } from "../../api/client";
import { useEventStore } from "../../stores/eventStore";
import Panel from "../layout/Panel";

// 事件溯源时间线（README §5）：SSE 实时（eventStore.record 全量）+ 挂载时回填历史。
export default function TracePanel() {
  const events = useEventStore((s) => s.events);
  const loadHistory = useEventStore((s) => s.loadHistory);

  useEffect(() => {
    void getEventsLog({ limit: 200 }).then(loadHistory);
  }, [loadHistory]);

  return (
    <Panel title="溯源">
      <ul className="panel-list">
        {events.map((e) => (
          <li key={e.event_id} className="panel-item">
            <span className="panel-item__main">{e.event}</span>
            <span className="panel-item__meta">
              {new Date(e.received_at).toLocaleTimeString()} · {e.correlation_id}
            </span>
          </li>
        ))}
      </ul>
    </Panel>
  );
}
