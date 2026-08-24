import { useEffect } from "react";
import { useEvalStore } from "../../stores/evalStore";
import { SCORE_LABELS } from "../../lib/labels";
import Panel from "../layout/Panel";

// eval + token 看板（README §5）：无 SSE 事件，挂载拉取 + 「刷新」按钮。
// score 键名经 SCORE_LABELS 转中文。
export default function EvalPanel() {
  const reports = useEvalStore((s) => s.reports);
  const tokens = useEvalStore((s) => s.tokens);
  const error = useEvalStore((s) => s.error);
  const refresh = useEvalStore((s) => s.refresh);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const loaded = reports !== null && tokens !== null;

  return (
    <Panel title="Eval">
      <div className="panel-toolbar">
        <button type="button" className="panel-refresh" onClick={() => void refresh()}>
          刷新
        </button>
      </div>
      {error !== null && <p className="error-text">{error}</p>}
      {!loaded ? (
        "等待核心服务连接…"
      ) : (
        <div className="panel-list">
          <div className="panel-section">
            <h3 className="panel-section-title">eval 报告</h3>
            <ul className="panel-list">
              {reports.map((r) => (
                <li key={r.id} className="panel-item">
                  <span className="panel-item__main">
                    {r.module}/{r.type}
                  </span>
                  <span className="panel-item__meta">
                    {SCORE_LABELS.ooc}={r.scores.ooc.toFixed(2)}
                  </span>
                </li>
              ))}
            </ul>
          </div>
          <div className="panel-section">
            <h3 className="panel-section-title">token 明细</h3>
            <ul className="panel-list">
              {tokens.map((t) => (
                <li key={t.id} className="panel-item">
                  <span className="panel-item__main">
                    {t.module}/{t.purpose}
                  </span>
                  <span className="panel-item__meta">
                    {t.model} · in={t.input_tokens} out={t.output_tokens}
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
