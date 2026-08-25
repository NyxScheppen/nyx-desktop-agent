import { useState } from "react";
import { useChatStore } from "../../stores/chatStore";
import MessageList from "../chat/MessageList";
import EncounterCard from "../encounter/EncounterCard";
import MemoryPanel from "../panels/MemoryPanel";
import ReadingNotesPanel from "../panels/ReadingNotesPanel";

type ScrollMode = "chat" | "memory" | "notes";

// 书卷区域（design §5.1）：多模式（对话/记忆/笔记）滚动区，左下角模式切换按钮。
// 对话模式 = MessageList + EncounterCard（遭遇卡片钉在消息列表之后）。
// 记忆/笔记复用现有 MemoryPanel / ReadingNotesPanel（原侧边抽屉内容，此处平铺）。
export default function ScrollArea() {
  const [mode, setMode] = useState<ScrollMode>("chat");
  const messages = useChatStore((s) => s.messages);

  return (
    <section className="scroll-area">
      <div className="scroll-area__body">
        {mode === "chat" && (
          <>
            <MessageList messages={messages} />
            <EncounterCard />
          </>
        )}
        {mode === "memory" && <MemoryPanel />}
        {mode === "notes" && <ReadingNotesPanel />}
      </div>
      <nav className="scroll-area__modes">
        {(
          [
            ["chat", "对话"],
            ["memory", "记忆"],
            ["notes", "笔记"],
          ] as const
        ).map(([key, label]) => (
          <button
            key={key}
            type="button"
            className={`scroll-area__mode${mode === key ? " scroll-area__mode--active" : ""}`}
            aria-pressed={mode === key}
            onClick={() => setMode(key)}
          >
            {label}
          </button>
        ))}
      </nav>
    </section>
  );
}
