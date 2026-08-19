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
    setText("");
    void sendMessage(trimmed);
  };

  return (
    <div className="chat-input">
      <input
        className="chat-input__field"
        value={text}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter") submit();
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
      {sendError !== null && <p className="chat-input__error">{sendError}</p>}
    </div>
  );
}
