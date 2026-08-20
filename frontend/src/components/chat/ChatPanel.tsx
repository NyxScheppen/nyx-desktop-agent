import { useChatStore } from "../../stores/chatStore";
import ChatInput from "./ChatInput";
import MessageList from "./MessageList";

// 右侧聊天窗（视觉改造布局 §1）：微信式大窗，消息列表上滑 + 底部输入框。
// 仍只订阅 messages 透传；SSE 挂 App 层，本组件只消费 store。
export default function ChatPanel() {
  const messages = useChatStore((s) => s.messages);
  return (
    <section className="dialog-box">
      <header className="dialog-box__header">
        <h2 className="dialog-box__name">Nyx</h2>
      </header>
      <MessageList messages={messages} />
      <ChatInput />
    </section>
  );
}
