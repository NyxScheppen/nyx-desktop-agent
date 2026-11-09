import { useEffect, useState } from "react";
import { cancelResearch, resumeResearch } from "../../api/client";
import { useBehaviorStore, type ResearchRun } from "../../stores/behaviorStore";
import Panel from "../layout/Panel";
import { formatDateTime } from "../../lib/time";

const STATUS: Record<string, string> = {
  uncertain: "写入待确认", invalid_output: "反思格式校验失败",
  completed: "已完成", failed: "失败", paused: "已暂停", cancelled: "已取消",
  waiting_user: "等待补充", deciding: "研究中", queued: "排队中", persisted: "经验已保存",
  pending: "待处理", updated: "已修改", no_change: "无需修改",
  insufficient_evidence: "证据不足", conflict: "版本冲突，待重新复核",
  success: "完成", running: "执行中", interrupted: "中断", rejected: "拒绝", waiting: "等待",
  wait_user: "等待用户回答", cancel_requested: "正在取消",
};
const NODES: Record<string, string> = {
  select_topic: "选题", decide: "选择步骤", rewrite: "改写问题", follow_up: "提出追问",
  knowledge_search: "检索已有知识", search: "搜索／补搜", read: "读取资料",
  source_check: "资料准入", evidence: "保存合格证据", digest: "消化资料",
  commit: "提交知识", reject: "拒绝资料", summarize: "整理结论", settle: "结算",
  finish: "结束研究", ask_user: "询问用户", user_answer: "用户补充",
  wait_user: "等待用户回答",
};
function at(value: unknown): string {
  return typeof value === "number" ? formatDateTime(value) : "—";
}
function raw(value: unknown): string { return JSON.stringify(value, null, 2); }

export function ResearchTrace({ run }: { run: ResearchRun }) {
  const [answer, setAnswer] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const checkpoint = run.activity.progress.exploration;
  return <details className="panel-item">
    <summary>{run.question} · {STATUS[run.status] ?? run.status}</summary>
    <p>{run.origin === "dialogue" ? "对话研究" : "自主探索"} · 开始于 {at(run.created_at)}</p>
    {run.last_error && <p role="alert">{run.last_error}</p>}
    {run.result && <details><summary>结论、证据与未解决问题</summary>
      <pre style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere", maxHeight: "28rem", overflow: "auto" }}>{raw(run.result)}</pre></details>}
    <ol>{(checkpoint.node_trace ?? []).map((node, index) => <li key={index}>
      {NODES[String(node.node)] ?? String(node.node)} · {STATUS[String(node.status)] ?? String(node.status)}
      <small> {at(node.started_at)} → {at(node.completed_at)}</small>
    </li>)}</ol>
    <details><summary>选题、查询、追问与执行原文</summary><pre style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere", maxHeight: "28rem", overflow: "auto" }}>{raw(checkpoint.actions)}</pre></details>
    {run.status === "waiting_user" && <p>{checkpoint.waiting_question}</p>}
    {(["paused", "uncertain", "waiting_user"].includes(run.status)) &&
      <form onSubmit={(event) => {
        event.preventDefault(); setBusy(true); setError("");
        void resumeResearch(run.id, answer).then(() => useBehaviorStore.getState().refresh())
          .catch((reason: unknown) => setError(reason instanceof Error ? reason.message : String(reason)))
          .finally(() => setBusy(false));
      }}>
        {run.status === "waiting_user" && <input aria-label="补充研究信息" className="panel-input"
          value={answer} onChange={(event) => setAnswer(event.target.value)} required />}
        <button disabled={busy} type="submit">{busy ? "研究中…" : "继续研究"}</button>
      </form>}
    {!["completed", "failed", "cancelled"].includes(run.status) && <button disabled={busy}
      type="button" onClick={() => {
        setBusy(true); setError("");
        void cancelResearch(run.id).then(() => useBehaviorStore.getState().refresh())
          .catch((reason: unknown) => setError(reason instanceof Error ? reason.message : String(reason)))
          .finally(() => setBusy(false));
      }}>取消研究</button>}
    {error && <p role="alert">{error}</p>}
  </details>;
}

export default function BehaviorPanel() {
  const { runs, reflections, versions, error, refresh } = useBehaviorStore();
  useEffect(() => { void refresh(); }, [refresh]);
  return <Panel title="研究与行为反思">
    <button onClick={() => void refresh()}>刷新记录</button>
    {error && <p role="alert">{error}</p>}
    <h3>研究路径</h3>
    {runs.length ? runs.map((run) => <ResearchTrace key={run.id} run={run} />) : <p>尚无研究记录</p>}
    <h3>行为反思与经验</h3>
    {reflections.map((record) => <details key={record.id} className="panel-item">
      <summary>{record.target_skill === "discussion" ? "探索／讨论研究" : "普通对话"}
        {" · "}{STATUS[record.status] ?? record.status} · {STATUS[record.skill_update_status] ?? record.skill_update_status}</summary>
      <p>触发：{at(record.created_at)} · 反思开始：{at(record.reflection_started_at)}<br />
        经验保存：{at(record.persisted_at)} · 修改提交：{at(record.skill_updated_at)}</p>
      <p>使用版本 {record.used_skill_version}{record.applied_skill_version ? ` → 新版本 ${record.applied_skill_version}` : ""}</p>
      {record.last_error && <p role="alert">{record.last_error}</p>}
      {record.experience_content_raw && <details><summary>长期经验入库原文</summary>
        <pre style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere", maxHeight: "28rem", overflow: "auto" }}>{record.experience_content_raw}</pre></details>}
      {!!record.model_attempts?.length && <details><summary>未通过校验的反思原文</summary>
        <pre style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere", maxHeight: "28rem", overflow: "auto" }}>{raw(record.model_attempts)}</pre></details>}
      <details><summary>原始证据</summary><pre style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere", maxHeight: "28rem", overflow: "auto" }}>{raw(record.evidence)}</pre></details>
      <details><summary>行为反思与策略建议</summary><pre style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere", maxHeight: "28rem", overflow: "auto" }}>{raw(record.reflection)}</pre></details>
      <details><summary>反思模型原始输出</summary><pre style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere", maxHeight: "28rem", overflow: "auto" }}>{record.reflection_output_raw ?? "尚未生成"}</pre></details>
      {record.skill_diff && <details><summary>已提交的 skill 差异</summary><pre style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere", maxHeight: "28rem", overflow: "auto" }}>{record.skill_diff}</pre></details>}
      <details><summary>处理日志</summary><pre style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere", maxHeight: "28rem", overflow: "auto" }}>{raw(record.logs)}</pre></details>
    </details>)}
    <h3>Skill 版本历史</h3>
    {Object.entries(versions).map(([name, records]) => <section key={name}>
      <h4>{name === "discussion" ? "探索／讨论研究" : "普通对话"}</h4>
      {records.map((version) => <details key={version.version} className="panel-item">
        <summary>版本 {version.version} · {at(version.created_at)}</summary>
        <pre style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere", maxHeight: "28rem", overflow: "auto" }}>{version.body}</pre>
        {version.reflection_id && <small>关联行为反思：{version.reflection_id}</small>}
      </details>)}
    </section>)}
  </Panel>;
}
