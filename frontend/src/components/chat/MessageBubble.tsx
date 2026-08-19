import { useState } from "react";
import type { ChatMessage } from "../../stores/chatStore";
import EmotionSprite from "../inner/EmotionSprite";

type MessageBubbleProps = {
  message: ChatMessage;
};

// 单条气泡按 role/kind 渲染（03-chat-panel §3）：
// speak/ask/initiate_chat 挂当前情绪 sprite；think 默认折叠点开看；initiate_chat 带「搭话」标记。
export default function MessageBubble({ message }: MessageBubbleProps) {
  const [thinkExpanded, setThinkExpanded] = useState(false);
  const { role, kind, content } = message;
  const className = `message-bubble message-bubble--${role} message-bubble--${kind}`;

  if (kind === "think") {
    return (
      <div className={className}>
        {thinkExpanded ? (
          <span className="message-bubble__content">{content}</span>
        ) : (
          <button
            type="button"
            className="message-bubble__toggle"
            onClick={() => setThinkExpanded(true)}
          >
            内心话
          </button>
        )}
      </div>
    );
  }

  const showSprite = kind === "speak" || kind === "ask" || kind === "initiate_chat";

  return (
    <div className={className}>
      {role === "nyx" && showSprite && <EmotionSprite size="small" />}
      {kind === "initiate_chat" && <span className="message-bubble__badge">搭话</span>}
      <span className="message-bubble__content">{content}</span>
    </div>
  );
}
