import { useState, type FormEvent } from "react";
import { postKnowledgeSource } from "../../api/client";
import { captureScope, isScopeCurrent } from "../../api/characterScope";

export function KnowledgeFeed() {
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
        setError(`已保存 ${result.chunks.length} 块原文，部分摄入未完成：${result.errors.join("；")}`);
      } else {
        setMessage(`已进知识库 · 新增 ${result.created_memories} 条知识、${result.created_facts} 条事实`);
        setText("");
        setUrl("");
      }
    } catch (cause) {
      if (isScopeCurrent(scope)) setError(cause instanceof Error ? cause.message : "摄入失败，请重试");
    } finally {
      if (isScopeCurrent(scope)) setSubmitting(false);
    }
  }

  return (
    <details className="knowledge-feed">
      <summary>投喂知识</summary>
      <form onSubmit={(event) => void submit(event)}>
        <label>文本<textarea aria-label="知识文本" maxLength={200000} value={text}
          onChange={(event) => setText(event.target.value)} disabled={submitting} /></label>
        <label>网页 URL<input aria-label="知识网页 URL" type="url" maxLength={2048} value={url}
          onChange={(event) => setUrl(event.target.value)} disabled={submitting} /></label>
        <button type="submit" disabled={submitting || (!text.trim() && !url.trim())}>
          {submitting ? "正在摄入…" : "进知识库"}
        </button>
        {message && <p role="status">{message}</p>}
        {error && <p role="alert">{error}</p>}
      </form>
    </details>
  );
}
