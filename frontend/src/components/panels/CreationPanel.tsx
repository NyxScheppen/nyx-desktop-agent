import frontendPolicy from "../../../../nyx/settings/frontend.json";
import { formatDateTime } from "../../lib/time";
import { useEffect, useState } from "react";
import {
  formatOutputBody,
  formatOutputPath,
  formatResult,
} from "../../lib/activityResult";
import { useActivityStore } from "../../stores/activityStore";
import Panel from "../layout/Panel";
import FlowPages from "../layout/FlowPages";

function dateTimeLabel(timestamp: number): string {
  return formatDateTime(timestamp);
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
    <Panel title={frontendPolicy.copy.CreationPanel.title} className="paged-panel creation-panel">
      {error !== null && <p className="error-text">{error}</p>}
      <FlowPages key={selected?.id ?? "shelf"} onLoadMore={!selected && hasMore ? () => void loadMore() : undefined} loading={loading}>
      {creations === null ? (
        frontendPolicy.copy.CreationPanel.text
      ) : creations.length === 0 ? (
        <p className="panel-item">{frontendPolicy.copy.CreationPanel.p}</p>
      ) : selected !== undefined ? (
        <article className="creation-detail">
          <button type="button" className="reader-tool" onClick={() => setSelectedId(null)}>{frontendPolicy.copy.CreationPanel.button}</button>
          <h3>{formatResult(selected)}</h3>
          <time>{dateTimeLabel(selected.ended_at ?? selected.started_at)}</time>
          <p className="panel-item__meta">{frontendPolicy.copy.CreationPanel.p_2}{selected.id}</p>
          <div className="creation-record__body">{formatOutputBody(selected)}</div>
          {formatOutputPath(selected) !== null && <p className="panel-item__meta" title={formatOutputPath(selected) ?? undefined}>{frontendPolicy.copy.CreationPanel.p_3}{formatOutputPath(selected)}</p>}
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
      </FlowPages>
    </Panel>
  );
}
