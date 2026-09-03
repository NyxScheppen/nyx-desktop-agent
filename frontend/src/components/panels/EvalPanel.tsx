import { useEffect } from "react";
import { label, OUTPUT_TYPE_LABELS } from "../../lib/labels";
import { useEvalStore } from "../../stores/evalStore";
import Panel from "../layout/Panel";

// LLM 调用 / token 面板（15-eval）：总 token + 最近 5 条调用（类型 + OOC 分 + token）。
export default function EvalPanel() {
  const records = useEvalStore((s) => s.records);
  const stats = useEvalStore((s) => s.stats);
  const error = useEvalStore((s) => s.error);
  const refresh = useEvalStore((s) => s.refresh);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  return (
    <Panel title="LLM 调用 / token">
      {error !== null && <p className="error-text">{error}</p>}
      {stats !== null && (
        <p className="panel-item">
          <span className="panel-item__main">总 token</span>
          <span className="panel-item__meta">
            {stats.total_tokens}（prompt {stats.prompt_tokens} / completion{" "}
            {stats.completion_tokens}）
          </span>
        </p>
      )}
      {records === null ? (
        "等待核心服务连接…"
      ) : records.length === 0 ? (
        <p className="panel-item">还没有 LLM 调用记录</p>
      ) : (
        <ul className="panel-list">
          {records.map((r) => (
            <li key={r.id} className="panel-item">
              <span className="panel-item__main">
                {label(OUTPUT_TYPE_LABELS, r.output_type)}
              </span>
              <span className="panel-item__meta">
                OOC {r.ooc_keyword.toFixed(2)}
                {r.ooc_embed !== null ? ` · embed ${r.ooc_embed.toFixed(2)}` : ""}
                {" · "}
                {r.prompt_tokens}+{r.completion_tokens} token
              </span>
            </li>
          ))}
        </ul>
      )}
    </Panel>
  );
}
