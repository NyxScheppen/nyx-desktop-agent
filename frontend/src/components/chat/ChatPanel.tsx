import { useEffect } from "react";
import { useChatStore } from "../../stores/chatStore";
import { CATEGORIES } from "../layout/InnerWorld";
import ChatInput from "./ChatInput";
import MessageList from "./MessageList";

type ChatPanelProps = {
  onOpenSettings: () => void;
  onOpenInner: (categoryIndex: number) => void;
};

// 右侧聊天窗（视觉改造布局 §1）：微信式大窗，消息列表上滑 + 底部输入框。
// 头部「内在/空间/记录」三个按钮开对应分类的内心世界卡片、「设置」按钮切到设置面板（App 层状态切换）。
// 仍只订阅 messages 透传；SSE 挂 App 层，本组件只消费 store。
// 挂载时回填历史消息（03 §4），preloaded 不逐字；实时 SSE 仍照常 append。
export default function ChatPanel({ onOpenSettings, onOpenInner }: ChatPanelProps) {
  const messages = useChatStore((s) => s.messages);
  const loadHistory = useChatStore((s) => s.loadHistory);

  useEffect(() => {
    void loadHistory();
  }, [loadHistory]);

  return (
    <section className="dialog-box">
      <header className="dialog-box__header">
        <h2 className="dialog-box__name">Nyx</h2>
        <div className="dialog-box__actions">
          {CATEGORIES.map((c, i) => (
            <button
              key={c.label}
              type="button"
              className="dialog-box__inner"
              onClick={() => onOpenInner(i)}
            >
              {c.label}
            </button>
          ))}
          <button type="button" className="dialog-box__settings" onClick={onOpenSettings}>
            设置
          </button>
        </div>
      </header>
      <MessageList messages={messages} />
      <ChatInput />
    </section>
  );
}
