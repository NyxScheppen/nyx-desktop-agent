import { useEffect } from "react";
import { ACTIVITY_TYPE_LABELS, ACTIVITY_STATUS_LABELS } from "../../lib/labels";
import { formatResult } from "../../lib/activityResult";
import { useActivityStore } from "../../stores/activityStore";
import Panel from "../layout/Panel";

// 活动时间线面板（README §5）：REST 快照 + SSE activity_* 触发 refresh。
// 「当前」与「日程」合并为单条时间线——后端 schedule 本就是「今日已产生记录」
// （started_at ASC），running 也在其中；running 加「◀ 现在」标记，不画未来空槽。
// 产出区只列最近 12 条创作标题摘要；完整正文在「创作」页查看。
function timeLabel(ts: number): string {
  return new Date(ts * 1000).toLocaleTimeString();
}

export default function ActivityPanel() {
  const data = useActivityStore((s) => s.data);
  const results = useActivityStore((s) => s.results);
  const error = useActivityStore((s) => s.error);
  const refresh = useActivityStore((s) => s.refresh);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const timeline = data === null ? [] : [...data.schedule];
  const creationResults =
    results?.filter((activity) => activity.type === "creation").slice(0, 12) ??
    null;

  return (
    <Panel title="活动">
      {error !== null && <p className="error-text">{error}</p>}
      {data === null ? (
        "等待核心服务连接…"
      ) : timeline.length === 0 ? (
        <p className="panel-item">今天还没有活动记录</p>
      ) : (
        <ul className="timeline">
          {timeline.map((a) => {
            const isCurrent = a.status === "running";
            const result = formatResult(a);
            return (
              <li
                key={a.id}
                className={
                  isCurrent
                    ? "timeline__item timeline__item--current"
                    : "timeline__item"
                }
              >
                <span className="timeline__dot" />
                <div className="panel-item">
                  <span className="panel-item__main">
                    {ACTIVITY_TYPE_LABELS[a.type]}{" "}
                    <span className="panel-badge">
                      {ACTIVITY_STATUS_LABELS[a.status]}
                    </span>
                    {isCurrent && <span className="timeline__now">◀ 现在</span>}
                  </span>
                  <span className="panel-item__meta">
                    {timeLabel(a.started_at)}
                  </span>
                  {result !== null && (
                    <span className="panel-item__meta">{result}</span>
                  )}
                </div>
              </li>
            );
          })}
        </ul>
      )}
      {creationResults !== null && creationResults.length > 0 && (
        <div className="outputs">
          <h3 className="outputs__title">产出</h3>
          <ul className="outputs__list">
            {creationResults.map((a) => {
              const title = formatResult(a);
              if (title === null) return null;
              return (
                <li key={a.id} className="panel-item">
                  <span className="panel-item__main">{title}</span>
                  <span className="panel-item__meta">
                    {timeLabel(a.ended_at ?? a.started_at)}
                  </span>
                </li>
              );
            })}
          </ul>
        </div>
      )}
    </Panel>
  );
}
