import { useEffect, useRef } from "react";
import { useChatStore } from "../../stores/chatStore";
import type { ChatMessage } from "../../stores/chatStore";
import MessageBubble from "./MessageBubble";

type MessageListProps = {
  messages: ChatMessage[];
};

// nyx 文本消息种类（speak/ask/think/mutter/initiate_chat）：打字机的候选集合。
const NYX_TEXT_KINDS: ReadonlySet<ChatMessage["kind"]> = new Set([
  "speak",
  "ask",
  "think",
  "mutter",
  "initiate_chat",
]);

// 串行逐字（03 §3）：每条 nyx 文本消息等「同 correlation_id 且在其之前」的
// nyx 文本消息都打完（入 typedIds）才就绪；user 消息与 preloaded 历史恒就绪。
// 纯函数导出供测试。
export function isReady(
  message: ChatMessage,
  index: number,
  messages: ChatMessage[],
  typedIds: Record<string, true>,
): boolean {
  if (message.preloaded || !NYX_TEXT_KINDS.has(message.kind)) {
    return true;
  }
  return !messages.slice(0, index).some(
    (m) =>
      NYX_TEXT_KINDS.has(m.kind) &&
      !m.preloaded &&
      m.correlation_id === message.correlation_id &&
      !typedIds[m.id],
  );
}

// 微信式列表（视觉改造）：全部消息按序渲染，随内容增长同步滚到底——新消息
// 与打字机逐字都触发（见下方 MutationObserver）。历史往上滑看（滚动条隐藏，见 index.css）。
export default function MessageList({ messages }: MessageListProps) {
  const typedIds = useChatStore((s) => s.typedIds);
  const markTyped = useChatStore((s) => s.markTyped);
  const listRef = useRef<HTMLDivElement | null>(null);

  // 随内容增长滚到底：观察滚动容器自身 DOM 变化，新消息（childList）与
  // 打字机逐字（characterData）都触发。纯渲染层，不依赖 store/打字机状态。
  useEffect(() => {
    const el = listRef.current;
    if (!el) return;
    el.scrollTop = el.scrollHeight;
    const observer = new MutationObserver(() => {
      el.scrollTop = el.scrollHeight;
    });
    observer.observe(el, { childList: true, subtree: true, characterData: true });
    return () => observer.disconnect();
  }, []);

  return (
    <div className="message-list" ref={listRef}>
      {messages.map((m, i) => (
        <MessageBubble
          key={m.id}
          message={m}
          ready={isReady(m, i, messages, typedIds)}
          onTyped={markTyped}
        />
      ))}
    </div>
  );
}
