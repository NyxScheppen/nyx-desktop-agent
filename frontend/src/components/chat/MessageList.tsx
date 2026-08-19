import { useCallback, useEffect, useState } from "react";
import type { ChatMessage } from "../../stores/chatStore";
import MessageBubble from "./MessageBubble";

type MessageListProps = {
  messages: ChatMessage[];
};

// 串行打字 + 单条当前显示（视觉改造）：
// 一次只显示「当前一条」逐字，前一条打完（+停顿）才推进到下一条——
// 后端 THINK 先于 SPEAK 到达，故天然「先内心话后发言」；
// 更早的消息收进「历史」，点开回看全部。
const ADVANCE_DELAY_MS = 350; // 一句说完到下一句的停顿

export default function MessageList({ messages }: MessageListProps) {
  const [activeIdx, setActiveIdx] = useState(0);
  const [activeDone, setActiveDone] = useState(false);
  const [historyOpen, setHistoryOpen] = useState(false);

  // 会话重置（messages 清空）时复位
  useEffect(() => {
    if (messages.length === 0) {
      setActiveIdx(0);
      setActiveDone(false);
      setHistoryOpen(false);
    }
  }, [messages.length]);

  const handleDone = useCallback(() => setActiveDone(true), []);

  // 当前一句打完且后面还有 → 停顿后推进到下一条
  useEffect(() => {
    if (activeDone && activeIdx + 1 < messages.length) {
      const t = setTimeout(() => {
        setActiveIdx((i) => i + 1);
        setActiveDone(false);
      }, ADVANCE_DELAY_MS);
      return () => clearTimeout(t);
    }
  }, [activeDone, activeIdx, messages.length]);

  const history = messages.slice(0, activeIdx);
  const current = messages[activeIdx];

  return (
    <div className="message-list">
      {history.length > 0 && (
        <button
          type="button"
          className="history-toggle"
          onClick={() => setHistoryOpen((o) => !o)}
          aria-expanded={historyOpen}
        >
          {historyOpen ? "收起历史" : `历史（${history.length}）`}
        </button>
      )}
      {historyOpen && (
        <div className="message-list__history">
          {history.map((m) => (
            <MessageBubble key={m.id} message={m} animate={false} />
          ))}
        </div>
      )}
      {current !== undefined && (
        <MessageBubble key={current.id} message={current} onDone={handleDone} />
      )}
    </div>
  );
}
