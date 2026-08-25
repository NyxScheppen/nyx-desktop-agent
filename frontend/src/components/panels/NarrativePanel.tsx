import { useEffect } from "react";
import { useNarrativeStore } from "../../stores/narrativeStore";
import Panel from "../layout/Panel";

// 自我叙事面板：展示 Nyx 的 identity / story / self_view / becoming（GET /api/narrative）。
export default function NarrativePanel() {
  const data = useNarrativeStore((s) => s.data);
  const error = useNarrativeStore((s) => s.error);
  const highlightedStory = useNarrativeStore((s) => s.highlightedStory);
  const refresh = useNarrativeStore((s) => s.refresh);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  return (
    <Panel title="叙事">
      {error !== null && <p className="error-text">{error}</p>}
      {data === null ? (
        "等待核心服务连接…"
      ) : (
        <div className="panel-list">
          <p className="panel-item">{data.identity}</p>
          <div className="panel-section">
            <h3 className="panel-section-title">故事</h3>
            <ul className="panel-list">
              {data.story.map((s, i) => (
                <li key={i} className="panel-item">
                  {s}
                  {s === highlightedStory && (
                    <span className="panel-badge">新</span>
                  )}
                </li>
              ))}
            </ul>
          </div>
          <div className="panel-section">
            <h3 className="panel-section-title">自我认知</h3>
            <ul className="panel-list">
              {Object.entries(data.self_view).map(([k, v]) => (
                <li key={k} className="panel-item">
                  <span className="panel-item__main">{k}</span>
                  <span className="panel-item__meta">{v}</span>
                </li>
              ))}
            </ul>
          </div>
          <div className="panel-section">
            <h3 className="panel-section-title">成长</h3>
            <ul className="panel-list">
              {data.becoming.map((b, i) => (
                <li key={i} className="panel-item">
                  {b}
                </li>
              ))}
            </ul>
          </div>
        </div>
      )}
    </Panel>
  );
}
