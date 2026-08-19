import { useChatStore } from "../../stores/chatStore";
import Panel from "../layout/Panel";
import ChatInput from "./ChatInput";
import MessageList from "./MessageList";

// 聊天面板容器（03 §1）：订阅 messages 透传给 MessageList。SSE 挂 App 层，本组件只消费 store。
export default function ChatPanel() {
  const messages = useChatStore((s) => s.messages);
  return (
    <Panel title="聊天">
      <MessageList messages={messages} />
      <ChatInput />
    </Panel>
  );
}
