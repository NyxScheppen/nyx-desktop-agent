import { useEffect, useState } from "react";
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
  const [selectedId, setSelectedId] = useState<string | null>(null);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const creations = results?.filter((activity) => activity.type === "creation") ?? null;
  const selected = creations?.find((activity) => activity.id === selectedId);

  return (
    <Panel title="创作">
      {error !== null && <p className="error-text">{error}</p>}
      {creations === null ? (
        "等待核心服务连接…"
      ) : creations.length === 0 ? (
        <p className="panel-item">当前角色还没有完成创作</p>
      ) : selected !== undefined ? (
        <article className="creation-detail">
          <button type="button" className="reader-tool" onClick={() => setSelectedId(null)}>← 返回书架</button>
          <h3>{formatResult(selected)}</h3>
          <time>{dateTimeLabel(selected.ended_at ?? selected.started_at)}</time>
          <p className="panel-item__meta">来自创作活动 · {selected.id}</p>
          <div className="creation-record__body">{formatOutputBody(selected)}</div>
          {formatOutputPath(selected) !== null && <p className="panel-item__meta" title={formatOutputPath(selected) ?? undefined}>来源：{formatOutputPath(selected)}</p>}
        </article>
      ) : (
        <ul className="creation-shelf">
          {creations.map((activity) => {
            const title = formatResult(activity);
            const body = formatOutputBody(activity);
            if (title === null || body === null) return null;
            return (
              <li key={activity.id} className="creation-shelf__item">
                <button type="button" onClick={() => setSelectedId(activity.id)}>
                  <span className="creation-shelf__monogram" aria-hidden="true">{title.charAt(0)}</span>
                  <span className="panel-item__main">{title}</span>
                  <span className="panel-item__meta">{dateTimeLabel(activity.ended_at ?? activity.started_at)}</span>
                </button>
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
