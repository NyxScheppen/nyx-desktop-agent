import { useTypewriter } from "../../hooks/useTypewriter";
import type { ChatMessage } from "../../stores/chatStore";

type MessageBubbleProps = {
  message: ChatMessage;
};

// 单条气泡按 role/kind 渲染（03-chat-panel §3 + 视觉改造打字机 §4）：
// nyx 文本消息（speak/ask/think/mutter/initiate_chat）走 useTypewriter 逐字，打字时末尾闪烁光标；
// user 消息即时；think 弱化（灰色斜体小字）。
// 不再挂情绪 sprite（视觉改造：尼克斯消息旁只显示信息）。
export default function MessageBubble({ message }: MessageBubbleProps) {
  const { role, kind, content } = message;
  const isNyxText =
    kind === "speak" ||
    kind === "ask" ||
    kind === "think" ||
    kind === "mutter" ||
    kind === "initiate_chat";
  const { displayed, done } = useTypewriter(isNyxText ? content : "");
  const text = isNyxText ? displayed : content;

  return (
    <div className={`message-bubble message-bubble--${role} message-bubble--${kind}`}>
      {kind === "initiate_chat" && <span className="message-bubble__badge">搭话</span>}
      <span className="message-bubble__content">
        {text}
        {isNyxText && !done && <span className="cursor-blink" />}
      </span>
    </div>
  );
}
