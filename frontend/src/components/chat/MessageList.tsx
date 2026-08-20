import { useEffect, useRef } from "react";
import type { ChatMessage } from "../../stores/chatStore";
import MessageBubble from "./MessageBubble";

type MessageListProps = {
  messages: ChatMessage[];
};

// 微信式列表（视觉改造）：全部消息按序渲染，最新消息自动滚到底；
// 历史往上滑看（滚动条隐藏，见 index.css）。每条消息独立逐字打字。
export default function MessageList({ messages }: MessageListProps) {
  const endRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    endRef.current?.scrollIntoView?.();
  }, [messages.length]);

  return (
    <div className="message-list">
      {messages.map((m) => (
        <MessageBubble key={m.id} message={m} />
      ))}
      <div ref={endRef} />
    </div>
  );
}
