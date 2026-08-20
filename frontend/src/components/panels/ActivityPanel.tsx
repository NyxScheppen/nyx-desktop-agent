import { useEffect } from "react";
import { useActivityStore } from "../../stores/activityStore";
import Panel from "../layout/Panel";

// 活动时间线面板（README §5）：REST 快照 + SSE activity_* 触发 refresh。
function timeLabel(ts: number): string {
  return new Date(ts * 1000).toLocaleTimeString();
}

export default function ActivityPanel() {
  const data = useActivityStore((s) => s.data);
  const error = useActivityStore((s) => s.error);
  const refresh = useActivityStore((s) => s.refresh);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  return (
    <Panel title="活动">
      {error !== null && <p className="error-text">{error}</p>}
      {data === null ? (
        "等待核心服务连接…"
      ) : (
        <div className="panel-list">
          <div className="panel-section">
            <h3 className="panel-section-title">当前</h3>
            {data.current === null ? (
              <p className="panel-item">空闲</p>
            ) : (
              <p className="panel-item">
                [{data.current.type}] {data.current.status} ·{" "}
                {timeLabel(data.current.started_at)}
              </p>
            )}
          </div>
          <div className="panel-section">
            <h3 className="panel-section-title">日程</h3>
            <ul className="panel-list">
              {data.schedule.map((a) => (
                <li key={a.id} className="panel-item">
                  <span className="panel-item__main">
                    [{a.type}] {a.status}
                  </span>
                  <span className="panel-item__meta">
                    {a.schedule_block_id} · {timeLabel(a.started_at)}
                  </span>
                </li>
              ))}
            </ul>
          </div>
        </div>
      )}
    </Panel>
  );
}
