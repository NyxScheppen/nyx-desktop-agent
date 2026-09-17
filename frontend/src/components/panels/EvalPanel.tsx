import { useEffect } from "react";
import { label, OUTPUT_TYPE_LABELS } from "../../lib/labels";
import { useEvalStore } from "../../stores/evalStore";
import type { EvalRecord } from "../../types/api";
import Panel from "../layout/Panel";

function EvalRecordItem({ record }: { record: EvalRecord }) {
  const prompts = useEvalStore((s) => s.prompts);
  const loading = useEvalStore((s) => Boolean(s.promptLoading[record.id]));
  const promptError = useEvalStore((s) => s.promptErrors[record.id]);
  const loadPrompt = useEvalStore((s) => s.loadPrompt);
  const loaded = Object.hasOwn(prompts, record.id);
  const prompt = prompts[record.id];

  return (
    <li className="panel-item eval-record">
      <details
        onToggle={(event) => {
          if (event.currentTarget.open) void loadPrompt(record.id);
        }}
      >
        <summary className="eval-record__summary">
          <span className="panel-item__main">
            {label(OUTPUT_TYPE_LABELS, record.output_type)}
          </span>
          <span className="panel-item__meta">
            OOC {record.ooc_keyword.toFixed(2)}
            {record.ooc_embed !== null
              ? ` · embed ${record.ooc_embed.toFixed(2)}`
              : ""}
            {" · "}
            {record.prompt_tokens}+{record.completion_tokens} token
          </span>
        </summary>
        <div className="eval-prompt" aria-live="polite">
          {loading ? (
            <p className="eval-prompt__status">正在加载 prompt…</p>
          ) : promptError !== undefined ? (
            <div className="eval-prompt__status error-text">
              <span>{promptError}</span>
              <button type="button" onClick={() => void loadPrompt(record.id)}>
                重试
              </button>
            </div>
          ) : loaded && prompt === null ? (
            <p className="eval-prompt__status">该记录未保存 prompt</p>
          ) : loaded && prompt?.length === 0 ? (
            <p className="eval-prompt__status">prompt 为空</p>
          ) : prompt !== undefined && prompt !== null ? (
            prompt.map((message, index) => (
              <section className="eval-prompt__message" key={`${index}-${message.role}`}>
                <h3>{message.role}</h3>
                <pre>{message.content}</pre>
              </section>
            ))
          ) : null}
        </div>
      </details>
    </li>
  );
}

// LLM 调用 / token 面板（10-eval）：总 token + 最近 5 条调用（类型 + OOC 分 + token）。
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
            <EvalRecordItem key={r.id} record={r} />
          ))}
        </ul>
      )}
    </Panel>
  );
}
