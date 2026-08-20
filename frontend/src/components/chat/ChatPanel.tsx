import { useEffect } from "react";
import { useChatStore } from "../../stores/chatStore";
import ChatInput from "./ChatInput";
import MessageList from "./MessageList";

// 右侧聊天窗（视觉改造布局 §1）：微信式大窗，消息列表上滑 + 底部输入框。
// 仍只订阅 messages 透传；SSE 挂 App 层，本组件只消费 store。
// 挂载时回填历史消息（03 §4），preloaded 不逐字；实时 SSE 仍照常 append。
export default function ChatPanel() {
  const messages = useChatStore((s) => s.messages);
  const loadHistory = useChatStore((s) => s.loadHistory);

  useEffect(() => {
    void loadHistory();
  }, [loadHistory]);

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
