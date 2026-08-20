import { useEffect, useRef } from "react";
import { useChatStore } from "../../stores/chatStore";
import type { ChatMessage } from "../../stores/chatStore";
import MessageBubble from "./MessageBubble";

type MessageListProps = {
  messages: ChatMessage[];
};

// 串行逐字（03 §3）：speak/ask 需等「同 correlation_id 且在其之前的 think」都打完才开打。
// 纯函数导出供测试；preloaded 历史消息一律就绪（不逐字）。
export function isReady(
  message: ChatMessage,
  index: number,
  messages: ChatMessage[],
  typedIds: Record<string, true>,
): boolean {
  if (message.preloaded || (message.kind !== "speak" && message.kind !== "ask")) {
    return true;
  }
  return !messages.slice(0, index).some(
    (m) => m.kind === "think" && m.correlation_id === message.correlation_id && !typedIds[m.id],
  );
}

// 微信式列表（视觉改造）：全部消息按序渲染，最新消息自动滚到底；
// 历史往上滑看（滚动条隐藏，见 index.css）。每条消息独立逐字打字。
export default function MessageList({ messages }: MessageListProps) {
  const typedIds = useChatStore((s) => s.typedIds);
  const markTyped = useChatStore((s) => s.markTyped);
  const endRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    endRef.current?.scrollIntoView?.();
  }, [messages.length]);

  return (
    <div className="message-list">
      {messages.map((m, i) => (
        <MessageBubble
          key={m.id}
          message={m}
          ready={isReady(m, i, messages, typedIds)}
          onThinkTyped={markTyped}
        />
      ))}
      <div ref={endRef} />
    </div>
  );
}
