import { useEffect } from "react";
import { useDesireStore } from "../../stores/desireStore";
import { DESIRE_TYPE_LABELS, DESIRE_STATUS_LABELS } from "../../lib/labels";
import Panel from "../layout/Panel";

// 欲望面板（README §5）：REST 快照 + SSE desire_* 触发 refresh。
// 枚举值经 DESIRE_TYPE_LABELS / DESIRE_STATUS_LABELS 转中文。
export default function DesiresPanel() {
  const data = useDesireStore((s) => s.data);
  const error = useDesireStore((s) => s.error);
  const refresh = useDesireStore((s) => s.refresh);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  return (
    <Panel title="欲望">
      {error !== null && <p className="error-text">{error}</p>}
      {data === null ? (
        "等待核心服务连接…"
      ) : (
        <div className="panel-list">
          {data.long_term.length > 0 && (
            <div className="panel-section">
              <h3 className="panel-section-title">长期欲望</h3>
              <ul className="panel-list">
                {data.long_term.map((d) => (
                  <li key={d.id} className="panel-item">
                    <span className="panel-item__main">
                      [{DESIRE_TYPE_LABELS[d.type]}] {d.name}
                    </span>
                    <span className="panel-item__meta">
                      strength={d.strength.toFixed(2)} progress=
                      {d.progress.toFixed(2)}
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          )}
          {data.short_term.length > 0 && (
            <div className="panel-section">
              <h3 className="panel-section-title">短期欲望</h3>
              <ul className="panel-list">
                {data.short_term.map((d) => (
                  <li key={d.id} className="panel-item">
                    <span className="panel-item__main">
                      [{DESIRE_TYPE_LABELS[d.type]}] {d.description}
                    </span>
                    <span className="panel-item__meta">
                      strength={d.strength.toFixed(2)} status={DESIRE_STATUS_LABELS[d.status]}
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          )}
          <div className="panel-section">
            <h3 className="panel-section-title">压力值</h3>
            <ul className="panel-list">
              {data.values.map((v) => (
                <li key={v.type} className="panel-item">
                  <span className="panel-item__main">{DESIRE_TYPE_LABELS[v.type]}</span>
                  <span className="panel-item__meta">value={v.value.toFixed(2)}</span>
                </li>
              ))}
            </ul>
          </div>
        </div>
      )}
    </Panel>
  );
}
