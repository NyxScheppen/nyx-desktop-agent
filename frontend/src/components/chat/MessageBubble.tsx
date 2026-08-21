import { useEffect } from "react";
import { useTypewriter } from "../../hooks/useTypewriter";
import type { ChatMessage } from "../../stores/chatStore";

type MessageBubbleProps = {
  message: ChatMessage;
  ready: boolean; // 串行逐字门控：等前置同 correlation_id 的 nyx 文本消息都打完才 true
  onTyped: (id: string) => void; // 逐字打完 → markTyped（供后续消息串行等待）
};

// 单条气泡按 role/kind 渲染（03-chat-panel §3 + 视觉改造打字机 §4）：
// nyx 文本消息（speak/ask/think/mutter/initiate_chat）走 useTypewriter 逐字，打字时末尾闪烁光标；
// user 消息即时；think 弱化（灰色斜体小字）。
// preloaded 历史消息直接全量显示不逐字；逐字打完回调 onTyped 标记，后续消息等 ready。
// 不再挂情绪 sprite（视觉改造：尼克斯消息旁只显示信息）。
export default function MessageBubble({ message, ready, onTyped }: MessageBubbleProps) {
  const { role, kind, content } = message;
  const isNyxText =
    kind === "speak" ||
    kind === "ask" ||
    kind === "think" ||
    kind === "mutter" ||
    kind === "initiate_chat";
  const preloaded = message.preloaded === true;
  // 非 preloaded 的 nyx 文本才逐字，其余即时全量显示
  const typewrite = isNyxText && !preloaded;
  const { displayed, done } = useTypewriter(typewrite ? content : "", 35, ready);
  const text = isNyxText ? (typewrite ? displayed : content) : content;
  // 等待期（ready=false）不显示光标：光标只在逐字进行时闪
  const showCursor = typewrite && ready && !done;

  useEffect(() => {
    if (typewrite && done) onTyped(message.id);
  }, [typewrite, done, message.id, onTyped]);

  return (
    <div className={`message-bubble message-bubble--${role} message-bubble--${kind}`}>
      {kind === "initiate_chat" && <span className="message-bubble__badge">搭话</span>}
      <span className="message-bubble__content">
        {text}
        {showCursor && <span className="cursor-blink" />}
      </span>
    </div>
  );
}
