import { useState } from "react";
import { useChatStore } from "../../stores/chatStore";

// 输入框 + 发送按钮（03 §1/§2）：isReplying 时仅禁用发送按钮（输入框可预打下一句）。
// 串行锁：回复中 submit 直接返回（memory chat-send-serial-input-lock）。
export default function ChatInput() {
  const [text, setText] = useState("");
  const isReplying = useChatStore((s) => s.isReplying);
  const sendError = useChatStore((s) => s.sendError);
  const sendMessage = useChatStore((s) => s.sendMessage);

  const submit = () => {
    if (isReplying) return;
    const trimmed = text.trim();
    if (trimmed === "") return;
    // 成功才清空（03 §2）：失败返回 false 保留原文；且只清「仍是原文」的框，
    // 否则回复期间预打的下一句会被无条件 setText("") 误删。比对 trim 后的值（框里可能还带原始空格）
    void sendMessage(trimmed).then((ok) => {
      if (ok) setText((cur) => (cur.trim() === trimmed ? "" : cur));
    });
  };

  return (
    <div className="chat-input">
      <input
        className="chat-input__field"
        value={text}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => {
          // 中文输入法回车确认候选字也会派发 keydown（isComposing=true），须跳过防误发送
          if (e.key === "Enter" && !e.nativeEvent.isComposing) submit();
        }}
        placeholder="对 Nyx 说…"
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
