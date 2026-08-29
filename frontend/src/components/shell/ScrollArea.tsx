import { useChatStore } from "../../stores/chatStore";
import MessageList from "../chat/MessageList";

// 书卷区域（design §5.1）：对话主舞台（MessageList）。
export default function ScrollArea() {
  const messages = useChatStore((s) => s.messages);

  return (
    <section className="scroll-area">
      <div className="scroll-area__body">
        <MessageList messages={messages} />
      </div>
    </section>
  );
}
