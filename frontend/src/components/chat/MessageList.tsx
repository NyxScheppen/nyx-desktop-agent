import { useEffect, useRef } from "react";
import type { ChatMessage } from "../../stores/chatStore";
import MessageBubble from "./MessageBubble";

type MessageListProps = {
  messages: ChatMessage[];
};

// 滚动列表（03 §1）：渲染 messages（由 ChatPanel 订阅透传），新消息自动滚到底。
// scrollIntoView 用 ?. 兜底 jsdom 未实现（测试环境无布局引擎）。
export default function MessageList({ messages }: MessageListProps) {
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView?.({ behavior: "smooth" });
  }, [messages]);

  return (
    <div className="message-list">
      {messages.map((m) => (
        <MessageBubble key={m.id} message={m} />
      ))}
      <div ref={bottomRef} />
    </div>
  );
}
