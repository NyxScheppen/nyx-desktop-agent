import { captureScope, isScopeCurrent } from "../../api/characterScope";
import { useCharacterStore } from "../../stores/characterStore";
import { useChatStore } from "../../stores/chatStore";

// 输入框 + 发送按钮（03 §1/§2）：isReplying 时仅禁用发送按钮（输入框可预打下一句）。
// 串行锁：回复中 submit 直接返回（memory chat-send-serial-input-lock）。
export default function ChatInput() {
  const text = useCharacterStore((s) => s.drafts[s.registry?.current_id ?? "nyx"] ?? "");
  const setText = useCharacterStore((s) => s.setDraft);
  const name = useCharacterStore((s) => s.registry?.characters.find((role) => role.id === s.registry?.current_id)?.name ?? "Nyx");
  const isReplying = useChatStore((s) => s.isReplying);
  const sendError = useChatStore((s) => s.sendError);
  const sendMessage = useChatStore((s) => s.sendMessage);
  const discussionMode = useChatStore((s) => s.discussionMode);
  const setDiscussionMode = useChatStore((s) => s.setDiscussionMode);
  const annotation = useChatStore((s) => s.annotationDraft);
  const updateComment = useChatStore((s) => s.updateComment);
  const cancelAnnotation = useChatStore((s) => s.cancelAnnotation);
  const sendAnnotation = useChatStore((s) => s.sendAnnotation);

  const submit = () => {
    if (isReplying) return;
    const trimmed = text.trim();
    if (trimmed === "") return;
    // 成功才清空（03 §2）：失败返回 false 保留原文；且只清「仍是原文」的框，
    // 否则回复期间预打的下一句会被无条件 setText("") 误删。比对 trim 后的值（框里可能还带原始空格）
    const scope = captureScope();
    void sendMessage(trimmed).then((ok) => {
      if (ok && isScopeCurrent(scope) && useCharacterStore.getState().drafts[scope?.id ?? "nyx"]?.trim() === trimmed) setText("");
    });
  };

  return (
    <div className="chat-input">
      <label className="chat-input__mode">
        <span>讨论模式</span>
        <select
          aria-label="讨论模式"
          value={discussionMode === true ? "on" : discussionMode === false ? "off" : "auto"}
          onChange={(event) => setDiscussionMode(event.target.value === "on" ? true : event.target.value === "off" ? false : null)}
        >
          <option value="auto">自动</option>
          <option value="on">开启</option>
          <option value="off">关闭</option>
        </select>
      </label>
      {annotation && (
        <div className="chat-input__annotation">
          <blockquote>{annotation.quote}</blockquote>
          <textarea
            aria-label="对回复的批注"
            value={annotation.comment}
            onChange={(event) => updateComment(event.target.value)}
            placeholder="写下你的看法、偏好或纠正…"
          />
          <small>{Array.from(annotation.comment).length}/2000</small>
          <button type="button" onClick={cancelAnnotation}>取消批注</button>
          <button type="button" disabled={isReplying || !annotation.comment.trim() || Array.from(annotation.comment).length > 2000}
            onClick={() => { void sendAnnotation(); }}>发送批注</button>
        </div>
      )}
      <input
        className="chat-input__field"
        value={text}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => {
          // 中文输入法回车确认候选字也会派发 keydown（isComposing=true），须跳过防误发送
          if (e.key === "Enter" && !e.nativeEvent.isComposing) submit();
        }}
        placeholder={`对 ${name} 说…`}
      />
      <button
        type="button"
        className="chat-input__send"
        disabled={isReplying}
        onClick={submit}
      >
        {isReplying ? "…" : "发送"}
      </button>
      {sendError !== null && <p className="error-text chat-input__error">{sendError}</p>}
    </div>
  );
}
