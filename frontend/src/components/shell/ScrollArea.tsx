import { useChatStore } from "../../stores/chatStore";
import MessageList from "../chat/MessageList";
import EncounterCard from "../encounter/EncounterCard";

// 书卷区域（design §5.1）：对话主舞台（MessageList + EncounterCard 遭遇卡片）。
// 记忆/笔记/资料等观测面板统一走左面板摘要入口（InnerWorld），此处不再多模式切换。
export default function ScrollArea() {
  const messages = useChatStore((s) => s.messages);

  return (
    <section className="scroll-area">
      <div className="scroll-area__body">
        <MessageList messages={messages} />
        <EncounterCard />
      </div>
    </section>
  );
}
