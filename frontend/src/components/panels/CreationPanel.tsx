import { useEffect } from "react";
import {
  formatOutputBody,
  formatOutputPath,
  formatResult,
} from "../../lib/activityResult";
import { useActivityStore } from "../../stores/activityStore";
import Panel from "../layout/Panel";

function dateTimeLabel(timestamp: number): string {
  return new Date(timestamp * 1000).toLocaleString();
}

export default function CreationPanel() {
  const results = useActivityStore((state) => state.results);
  const error = useActivityStore((state) => state.error);
  const loading = useActivityStore((state) => state.resultsLoading);
  const hasMore = useActivityStore((state) => state.hasMoreResults);
  const refresh = useActivityStore((state) => state.refresh);
  const loadMore = useActivityStore((state) => state.loadMoreResults);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const creations = results?.filter((activity) => activity.type === "creation") ?? null;

  return (
    <Panel title="创作">
      {error !== null && <p className="error-text">{error}</p>}
      {creations === null ? (
        "等待核心服务连接…"
      ) : creations.length === 0 ? (
        <p className="panel-item">尼克斯还没有完成创作</p>
      ) : (
        <ul className="outputs__list">
          {creations.map((activity) => {
            const title = formatResult(activity);
            const body = formatOutputBody(activity);
            const path = formatOutputPath(activity);
            if (title === null || body === null) return null;
            return (
              <li key={activity.id} className="panel-item creation-record">
                <details>
                  <summary className="creation-record__summary">
                    <span className="panel-item__main">{title}</span>
                    <span className="panel-item__meta">
                      {dateTimeLabel(activity.ended_at ?? activity.started_at)}
                    </span>
                  </summary>
                  <div className="creation-record__body">{body}</div>
                  {path !== null && (
                    <div className="panel-item__meta creation-record__path" title={path}>
                      {path}
                    </div>
                  )}
                </details>
              </li>
            );
          })}
        </ul>
      )}
      {hasMore && (
        <button
          type="button"
          className="outputs__more"
          disabled={loading}
          onClick={() => void loadMore()}
        >
          {loading ? "加载中…" : "加载更多"}
        </button>
      )}
    </Panel>
  );
}
