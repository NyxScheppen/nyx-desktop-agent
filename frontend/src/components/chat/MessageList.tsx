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

// nyx 文本消息种类（speak/ask/think/mutter/initiate_chat）：打字机的候选集合。
const NYX_TEXT_KINDS: ReadonlySet<ChatMessage["kind"]> = new Set([
  "speak",
  "ask",
  "think",
  "mutter",
  "initiate_chat",
]);

// 打字机只在「最开始」生效：仅第一条非 preloaded 的 nyx 文本消息逐字，
// 之后的消息默认即时全量显示（视觉改造：开头打字机、后续即时）。纯函数导出供测试。
export function isFirstTypewriter(index: number, messages: ChatMessage[]): boolean {
  return (
    messages.findIndex((m) => !m.preloaded && NYX_TEXT_KINDS.has(m.kind)) === index
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
          typewriter={isFirstTypewriter(i, messages)}
          onThinkTyped={markTyped}
        />
      ))}
      <div ref={endRef} />
    </div>
  );
}
