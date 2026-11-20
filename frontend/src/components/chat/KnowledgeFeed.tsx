import { formatPolicyText } from "../../lib/policy";
import memoryPolicy from "../../../../nyx/settings/memory.json";
import { useState, type FormEvent } from "react";
import { postKnowledgeSource } from "../../api/client";
import { captureScope, isScopeCurrent } from "../../api/characterScope";
import Panel from "../layout/Panel";

export function KnowledgeFeed({ embedded = false }: { embedded?: boolean }) {
  const [text, setText] = useState("");
  const [url, setUrl] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (submitting || (!text.trim() && !url.trim())) return;
    const scope = captureScope();
    if (!scope) return;
    setSubmitting(true);
    setMessage("");
    setError("");
    try {
      const result = await postKnowledgeSource({ text: text || undefined, url: url.trim() || undefined });
      if (!isScopeCurrent(scope)) return;
      if (result.errors?.length) {
        setError(formatPolicyText(memoryPolicy.templates.KnowledgeFeed.text, { p0: result.chunks.length, p1: result.errors.join("；") }));
      } else {
        setMessage(formatPolicyText(memoryPolicy.templates.KnowledgeFeed.text_2, { p0: result.created_memories, p1: result.created_facts }));
        setText("");
        setUrl("");
      }
    } catch (cause) {
      if (isScopeCurrent(scope)) setError(cause instanceof Error ? cause.message : memoryPolicy.copy.KnowledgeFeed.text);
    } finally {
      if (isScopeCurrent(scope)) setSubmitting(false);
    }
  }

  const content = (
      <div className="knowledge-feed">
      <form onSubmit={(event) => void submit(event)}>
        <label>{memoryPolicy.copy.KnowledgeFeed.label}<textarea aria-label={memoryPolicy.copy.KnowledgeFeed.aria_label} maxLength={memoryPolicy.limits.KnowledgeSourcePayload_text_max_length} value={text}
          onChange={(event) => setText(event.target.value)} disabled={submitting} /></label>
        <label>{memoryPolicy.copy.KnowledgeFeed.label_2}<input aria-label={memoryPolicy.copy.KnowledgeFeed.aria_label_2} type="url" maxLength={memoryPolicy.limits.KnowledgeSourcePayload_url_max_length} value={url}
          onChange={(event) => setUrl(event.target.value)} disabled={submitting} /></label>
        <button type="submit" disabled={submitting || (!text.trim() && !url.trim())}>
          {submitting ? memoryPolicy.copy.KnowledgeFeed.text_2 : memoryPolicy.copy.KnowledgeFeed.text_3}
        </button>
        {message && <p role="status">{message}</p>}
        {error && <p role="alert">{error}</p>}
      </form>
      </div>
  );
  if (embedded) return content;
  return (
    <Panel title={memoryPolicy.copy.KnowledgeFeed.title} className="knowledge-feed-panel">
      {content}
    </Panel>
  );
}
