import { useEffect } from "react";
import { useTypewriter } from "../../hooks/useTypewriter";
import type { ChatMessage } from "../../stores/chatStore";
import EmotionSprite from "../inner/EmotionSprite";

type MessageBubbleProps = {
  message: ChatMessage;
  animate?: boolean; // 是否逐字打字；历史消息 false 直接显示完整内容
  onDone?: () => void; // 打字完成后回调（MessageList 串行推进用）
};

// 单条气泡按 role/kind 渲染（03-chat-panel §3 + 视觉改造打字机 §4）：
// nyx 文本消息（speak/ask/think/mutter/initiate_chat）走 useTypewriter 逐字，打字时末尾闪烁光标；
// user 消息即时；think 弱化（灰色斜体小字，不再折叠）。done 时回调 onDone 供串行推进。
export default function MessageBubble({ message, animate = true, onDone }: MessageBubbleProps) {
  const { role, kind, content } = message;
  const isNyxText =
    kind === "speak" ||
    kind === "ask" ||
    kind === "think" ||
    kind === "mutter" ||
    kind === "initiate_chat";
  const shouldAnimate = animate && isNyxText;
  const { displayed, done } = useTypewriter(shouldAnimate ? content : "");
  const text = shouldAnimate ? displayed : content;
  const showSprite = kind === "speak" || kind === "ask" || kind === "initiate_chat";

  useEffect(() => {
    if (done) onDone?.();
  }, [done, onDone]);

  return (
    <div className={`message-bubble message-bubble--${role} message-bubble--${kind}`}>
      {role === "nyx" && showSprite && <EmotionSprite size="small" />}
      {kind === "initiate_chat" && <span className="message-bubble__badge">搭话</span>}
      <span className="message-bubble__content">
        {text}
        {shouldAnimate && !done && <span className="cursor-blink" />}
      </span>
    </div>
  );
}
