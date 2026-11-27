import { formatPolicyText } from "../../lib/policy";
import behaviorPolicy from "../../../../nyx/settings/behavior.json";
import { useEffect, useState } from "react";
import { cancelResearch, resumeResearch } from "../../api/client";
import { useBehaviorStore, type ResearchRun } from "../../stores/behaviorStore";
import Panel from "../layout/Panel";
import { formatDateTime } from "../../lib/time";

const STATUS: Record<string, string> = behaviorPolicy.frontend.BehaviorPanel.STATUS as Record<string, string>;
const NODES: Record<string, string> = behaviorPolicy.frontend.BehaviorPanel.NODES as Record<string, string>;
function at(value: unknown): string {
  return typeof value === "number" ? formatDateTime(value) : "—";
}
function raw(value: unknown): string { return JSON.stringify(value, null, 2); }

function researchStatus(run: ResearchRun): string {
  const outcome = run.result?.outcome;
  const key = run.status === "completed" && typeof outcome === "string" ? outcome : run.status;
  return STATUS[key] ?? key;
}

export function ResearchTrace({ run, showQuestion = true }: {
  run: ResearchRun;
  showQuestion?: boolean;
}) {
  const [answer, setAnswer] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const checkpoint = run.activity.progress.exploration;
  const result = run.result && Object.fromEntries(
    Object.entries(run.result).filter(([key, value]) =>
      key !== "question"
      && (!(key === "actions" || key === "tools") || !checkpoint.actions?.length)
      && (key !== "stop_reason" || value !== run.result?.failure_reason)),
  );
  return <details className="panel-item">
    <summary>{showQuestion ? run.question : at(run.created_at)} · {researchStatus(run)}</summary>
    <p>{run.origin === "dialogue" ? behaviorPolicy.copy.BehaviorPanel.text : behaviorPolicy.copy.BehaviorPanel.text_2}{behaviorPolicy.copy.BehaviorPanel.p}{at(run.created_at)}</p>
    {run.last_error && <p role="alert">{run.last_error}</p>}
    {result && <details><summary>{behaviorPolicy.copy.BehaviorPanel.summary}</summary>
      <pre style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere", maxHeight: "28rem", overflow: "auto" }}>{raw(result)}</pre></details>}
    <ol>{(checkpoint.node_trace ?? []).map((node, index) => <li key={index}>
      {NODES[String(node.node)] ?? String(node.node)} · {STATUS[String(node.status)] ?? String(node.status)}
      <small> {at(node.started_at)} → {at(node.completed_at)}</small>
    </li>)}</ol>
    <details><summary>{behaviorPolicy.copy.BehaviorPanel.summary_2}</summary><pre style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere", maxHeight: "28rem", overflow: "auto" }}>{raw(checkpoint.actions)}</pre></details>
    {run.status === "waiting_user" && <p>{checkpoint.waiting_question}</p>}
    {(["paused", "uncertain", "waiting_user"].includes(run.status)) &&
      <form onSubmit={(event) => {
        event.preventDefault(); setBusy(true); setError("");
        void resumeResearch(run.id, answer).then(() => useBehaviorStore.getState().refresh())
          .catch((reason: unknown) => setError(reason instanceof Error ? reason.message : String(reason)))
          .finally(() => setBusy(false));
      }}>
        {run.status === "waiting_user" && <input aria-label={behaviorPolicy.copy.BehaviorPanel.aria_label} className="panel-input"
          value={answer} onChange={(event) => setAnswer(event.target.value)} required />}
        <button disabled={busy} type="submit">{busy ? behaviorPolicy.copy.BehaviorPanel.text_3 : behaviorPolicy.copy.BehaviorPanel.text_4}</button>
      </form>}
    {!["completed", "failed", "cancelled"].includes(run.status) && <button disabled={busy}
      type="button" onClick={() => {
        setBusy(true); setError("");
        void cancelResearch(run.id).then(() => useBehaviorStore.getState().refresh())
          .catch((reason: unknown) => setError(reason instanceof Error ? reason.message : String(reason)))
          .finally(() => setBusy(false));
      }}>{behaviorPolicy.copy.BehaviorPanel.button}</button>}
    {error && <p role="alert">{error}</p>}
  </details>;
}

export default function BehaviorPanel() {
  const { runs, reflections, versions, error, refresh } = useBehaviorStore();
  useEffect(() => { void refresh(); }, [refresh]);
  const attempts = new Map<string, ResearchRun[]>();
  for (const run of runs) {
    const key = run.origin === "desire" ? run.correlation_id || run.id : run.id;
    const group = attempts.get(key);
    if (group) group.push(run);
    else attempts.set(key, [run]);
  }
  return <Panel title={behaviorPolicy.copy.BehaviorPanel.title}>
    <button onClick={() => void refresh()}>{behaviorPolicy.copy.BehaviorPanel.button_2}</button>
    {error && <p role="alert">{error}</p>}
    <h3>{behaviorPolicy.copy.BehaviorPanel.h3}</h3>
    {runs.length ? Array.from(attempts, ([key, group]) => <details key={key} className="panel-item">
      <summary>{group[0].question} · {researchStatus(group[0])}
        {group.length > 1 && <> · {formatPolicyText(
          behaviorPolicy.templates.BehaviorPanel.research_attempt_count, { p0: group.length },
        )}</>}
      </summary>
      {group.map((run) => <ResearchTrace key={run.id} run={run} showQuestion={false} />)}
    </details>) : <p>{behaviorPolicy.copy.BehaviorPanel.p_2}</p>}
    <h3>{behaviorPolicy.copy.BehaviorPanel.h3_2}</h3>
    {reflections.map((record) => <details key={record.id} className="panel-item">
      <summary>{record.target_skill === "discussion" ? behaviorPolicy.copy.BehaviorPanel.text_5 : behaviorPolicy.copy.BehaviorPanel.text_6}
        {" · "}{STATUS[record.status] ?? record.status} · {STATUS[record.skill_update_status] ?? record.skill_update_status}</summary>
      <p>{behaviorPolicy.copy.BehaviorPanel.p_3}{at(record.created_at)}{behaviorPolicy.copy.BehaviorPanel.p_4}{at(record.reflection_started_at)}<br />
        经验保存：{at(record.persisted_at)}{behaviorPolicy.copy.BehaviorPanel.p_5}{at(record.skill_updated_at)}</p>
      <p>{behaviorPolicy.copy.BehaviorPanel.p_6}{record.used_skill_version}{record.applied_skill_version ? formatPolicyText(behaviorPolicy.templates.BehaviorPanel.text, { p0: record.applied_skill_version }) : ""}</p>
      {record.last_error && <p role="alert">{record.last_error}</p>}
      {record.experience_content_raw && <details><summary>{behaviorPolicy.copy.BehaviorPanel.summary_3}</summary>
        <pre style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere", maxHeight: "28rem", overflow: "auto" }}>{record.experience_content_raw}</pre></details>}
      {!!record.model_attempts?.length && <details><summary>{behaviorPolicy.copy.BehaviorPanel.summary_4}</summary>
        <pre style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere", maxHeight: "28rem", overflow: "auto" }}>{raw(record.model_attempts)}</pre></details>}
      <details><summary>{behaviorPolicy.copy.BehaviorPanel.summary_5}</summary><pre style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere", maxHeight: "28rem", overflow: "auto" }}>{raw(record.evidence)}</pre></details>
      <details><summary>{behaviorPolicy.copy.BehaviorPanel.summary_6}</summary><pre style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere", maxHeight: "28rem", overflow: "auto" }}>{raw(record.reflection)}</pre></details>
      <details><summary>{behaviorPolicy.copy.BehaviorPanel.summary_7}</summary><pre style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere", maxHeight: "28rem", overflow: "auto" }}>{record.reflection_output_raw ?? behaviorPolicy.copy.BehaviorPanel.text_7}</pre></details>
      {record.skill_diff && <details><summary>{behaviorPolicy.copy.BehaviorPanel.summary_8}</summary><pre style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere", maxHeight: "28rem", overflow: "auto" }}>{record.skill_diff}</pre></details>}
      <details><summary>{behaviorPolicy.copy.BehaviorPanel.summary_9}</summary><pre style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere", maxHeight: "28rem", overflow: "auto" }}>{raw(record.logs)}</pre></details>
    </details>)}
    <h3>{behaviorPolicy.copy.BehaviorPanel.h3_3}</h3>
    {Object.entries(versions).map(([name, records]) => <section key={name}>
      <h4>{name === "discussion" ? behaviorPolicy.copy.BehaviorPanel.text_5 : behaviorPolicy.copy.BehaviorPanel.text_6}</h4>
      {records.map((version) => <details key={version.version} className="panel-item">
        <summary>{behaviorPolicy.copy.BehaviorPanel.summary_10}{version.version} · {at(version.created_at)}</summary>
        <pre style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere", maxHeight: "28rem", overflow: "auto" }}>{version.body}</pre>
        {version.reflection_id && <small>{behaviorPolicy.copy.BehaviorPanel.small}{version.reflection_id}</small>}
      </details>)}
    </section>)}
  </Panel>;
}
