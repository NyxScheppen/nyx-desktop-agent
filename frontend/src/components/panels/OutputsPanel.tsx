import { useEffect } from "react";
import { ACTIVITY_TYPE_LABELS } from "../../lib/labels";
import { activitySubject, formatOutputBody } from "../../lib/activityResult";
import { useActivityStore } from "../../stores/activityStore";
import Panel from "../layout/Panel";

// 产出面板（历史跨天）：读书笔记/探索发现/创作内容，按结束时间倒序。
// 数据同源 activityStore.results（README §5 双字段 refresh 一并拉取）。
// 每卡：主题（activitySubject）+ 类型·时间 + 正文（formatOutputBody 多行）。
function dateLabel(ts: number | null): string {
  return ts === null ? "—" : new Date(ts * 1000).toLocaleString();
}

export default function OutputsPanel() {
  const results = useActivityStore((s) => s.results);
  const error = useActivityStore((s) => s.error);
  const refresh = useActivityStore((s) => s.refresh);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  return (
    <Panel title="产出">
      {error !== null && <p className="error-text">{error}</p>}
      {results === null ? (
        "等待核心服务连接…"
      ) : results.length === 0 ? (
        <p className="panel-item">暂无产出</p>
      ) : (
        <ul className="panel-list">
          {results.map((a) => {
            const subject = activitySubject(a);
            const body = formatOutputBody(a);
            return (
              <li key={a.id} className="panel-item">
                <span className="panel-item__main">
                  {subject !== null ? subject : `[${ACTIVITY_TYPE_LABELS[a.type]}]`}
                </span>
                <span className="panel-item__meta">
                  [{ACTIVITY_TYPE_LABELS[a.type]}] · {dateLabel(a.ended_at)}
                </span>
                {body !== null && <span className="panel-item__body">{body}</span>}
              </li>
            );
          })}
        </ul>
      )}
    </Panel>
  );
}
