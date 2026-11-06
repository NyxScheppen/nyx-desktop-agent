import { useEffect, useRef, useState } from "react";
import { useTypewriter } from "../../hooks/useTypewriter";
import { useChatStore, type ChatMessage } from "../../stores/chatStore";

type MessageBubbleProps = {
  message: ChatMessage;
  ready: boolean; // 串行逐字门控：等前置同 correlation_id 的 nyx 文本消息都打完才 true
  onTyped: (id: string) => void; // 逐字打完 → markTyped（供后续消息串行等待）
};

// 单条气泡按 role/kind 渲染：
// nyx 文本消息（speak/ask/think/initiate_chat）走 useTypewriter 逐字，打字时末尾闪烁光标；
// user 消息即时；think 弱化（灰色斜体小字）。
// preloaded 历史消息直接全量显示不逐字；逐字打完回调 onTyped 标记，后续消息等 ready。
// 不再挂情绪 sprite（视觉改造：尼克斯消息旁只显示信息）。
export default function MessageBubble({ message, ready, onTyped }: MessageBubbleProps) {
  const { role, kind, content } = message;
  const isNyxText =
    kind === "speak" ||
    kind === "ask" ||
    kind === "think" ||
    kind === "initiate_chat";
  const preloaded = message.preloaded === true;
  const seen = useChatStore((s) => s.seenIds[message.id] === true);
  const markSeen = useChatStore((s) => s.markSeen);
  // 首次挂载的判定固定到本次挂载：标记已看不应打断正在播放的文字。
  const playOnThisMount = useRef(!seen);
  const typewrite = isNyxText && !preloaded && playOnThisMount.current;
  const { displayed, done } = useTypewriter(typewrite ? content : "", 35, ready);
  const text = isNyxText ? (typewrite ? displayed : content) : content;
  // 等待期（ready=false）不显示光标：光标只在逐字进行时闪
  const showCursor = typewrite && ready && !done;
  const selectQuestion = useChatStore((s) => s.selectQuestion);
  const pauseQuestion = useChatStore((s) => s.pauseQuestion);
  const resumeQuestion = useChatStore((s) => s.resumeQuestion);
  const beginAnnotation = useChatStore((s) => s.beginAnnotation);
  const annotationStatus = useChatStore((s) => message.annotation
    ? s.annotationPersistence[JSON.stringify([message.annotation.reply_event_id, message.annotation.comment])]
    : undefined);
  const contentRef = useRef<HTMLSpanElement>(null);
  const serverTimeOffset = useChatStore((s) => s.serverTimeOffset);
  const [now, setNow] = useState(() => Date.now() / 1000);
  const isWaitingInteraction = message.attempt_id !== undefined &&
    message.question_status === "waiting";
  const isQuestion = isWaitingInteraction &&
    (kind === "ask" || kind === "reading_question");
  const isTimedInteraction = isWaitingInteraction &&
    (kind === "ask" || kind === "reading_question" || kind === "initiate_chat");
  const canSelectInteraction = isTimedInteraction;

  useEffect(() => {
    if (!isTimedInteraction || message.paused_at !== null && message.paused_at !== undefined) return;
    const timer = window.setInterval(() => setNow(Date.now() / 1000), 250);
    return () => window.clearInterval(timer);
  }, [isTimedInteraction, message.paused_at]);

  const remaining = isTimedInteraction && message.expires_at !== undefined
    ? Math.max(
        0,
        message.paused_at !== null && message.paused_at !== undefined
          ? message.expires_at - message.paused_at
          : message.expires_at - now - serverTimeOffset,
      )
    : null;

  useEffect(() => {
    if (typewrite && ready) markSeen(message.id);
  }, [typewrite, ready, message.id, markSeen]);

  useEffect(() => {
    if ((typewrite && done) || (isNyxText && !preloaded && !playOnThisMount.current && ready)) {
      onTyped(message.id);
    }
  }, [typewrite, done, isNyxText, preloaded, ready, message.id, onTyped]);

  return (
    <div
      className={`message-bubble message-bubble--${role} message-bubble--${kind}`}
      onClick={canSelectInteraction ? () => selectQuestion(message.attempt_id ?? null) : undefined}
    >
      {kind === "initiate_chat" && <span className="message-bubble__badge">欲望搭话</span>}
      {kind === "reading_question" && <span className="message-bubble__badge">提问</span>}
      <span
        ref={contentRef}
        className="message-bubble__content"
        title={kind === "speak" ? "选择片段或点击全文来批注" : undefined}
        onClick={kind === "speak" && ready && (!typewrite || done) ? () => {
          const selection = window.getSelection();
          if (selection && !selection.isCollapsed) {
            if (selection.rangeCount !== 1) return;
            const range = selection.getRangeAt(0);
            if (!contentRef.current?.contains(range.startContainer) ||
                !contentRef.current.contains(range.endContainer)) return;
            beginAnnotation(message.id, selection.toString());
          } else {
            beginAnnotation(message.id, content);
          }
        } : undefined}
      >
        {text}
        {showCursor && <span className="cursor-blink" />}
      </span>
      {message.annotation && (
        <div className="message-bubble__annotation">
          <blockquote>{message.annotation.quote}</blockquote>
          <small>{annotationStatus === "recorded" ? "反馈已记录" : "反馈持久化待确认"}</small>
        </div>
      )}
      {kind === "reading_question" && message.selectedText && (
        <p className="message-bubble__quote">原文：「{message.selectedText}」</p>
      )}
      {isTimedInteraction && remaining !== null && (
        <span className="message-bubble__timer">
          {remaining > 0 ? `剩余 ${Math.ceil(remaining)} 秒` : "等待超时确认"}
          {message.paused_at !== null && message.paused_at !== undefined && "（已暂停）"}
        </span>
      )}
      {isQuestion && message.attempt_id !== undefined && (
        <button
          type="button"
          className="message-bubble__question-action"
          disabled={remaining === 0}
          onClick={(event) => {
            event.stopPropagation();
            void (message.paused_at !== null && message.paused_at !== undefined
              ? resumeQuestion(message.attempt_id as string)
              : pauseQuestion(message.attempt_id as string));
          }}
        >
          {message.paused_at !== null && message.paused_at !== undefined ? "继续计时" : "暂停计时"}
        </button>
      )}
    </div>
  );
}
