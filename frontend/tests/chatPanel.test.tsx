import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import ChatInput from "../src/components/chat/ChatInput";
import ChatPanel from "../src/components/chat/ChatPanel";
import MessageBubble from "../src/components/chat/MessageBubble";
import MessageList from "../src/components/chat/MessageList";
import { useChatStore, type ChatMessage } from "../src/stores/chatStore";
import { useInnerLifeStore } from "../src/stores/innerLifeStore";

let msgSeq = 0; // 自增保证 id 唯一，避免同 kind 两条消息撞 React key

function makeMsg(
  kind: ChatMessage["kind"],
  role: ChatMessage["role"],
  content: string,
): ChatMessage {
  msgSeq += 1;
  return { id: `id-${kind}-${msgSeq}`, role, kind, content, correlation_id: "c" };
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("MessageBubble", () => {
  beforeEach(() => {
    useInnerLifeStore.setState({ current: null, loading: false, error: null });
  });

  it("speak → 左气泡 class + content 上屏", () => {
    render(<MessageBubble message={makeMsg("speak", "nyx", "你好")} />);
    expect(screen.getByText("你好")).toHaveClass("message-bubble__content");
    expect(screen.getByText("你好").closest(".message-bubble")).toHaveClass(
      "message-bubble--speak",
    );
  });

  it("ask → 高亮 class", () => {
    render(<MessageBubble message={makeMsg("ask", "nyx", "想聊聊吗？")} />);
    expect(screen.getByText("想聊聊吗？").closest(".message-bubble")).toHaveClass(
      "message-bubble--ask",
    );
  });

  it("think → 默认折叠，点开才显示内容", () => {
    render(<MessageBubble message={makeMsg("think", "nyx", "内心独白")} />);
    expect(screen.queryByText("内心独白")).not.toBeInTheDocument();
    fireEvent.click(screen.getByText("内心话"));
    expect(screen.getByText("内心独白")).toBeInTheDocument();
  });

  it("initiate_chat → 带「搭话」标记", () => {
    render(<MessageBubble message={makeMsg("initiate_chat", "nyx", "在忙吗？")} />);
    expect(screen.getByText("搭话")).toBeInTheDocument();
  });

  it("user message → 右气泡 class", () => {
    render(<MessageBubble message={makeMsg("message", "user", "你好")} />);
    expect(screen.getByText("你好").closest(".message-bubble")).toHaveClass(
      "message-bubble--user",
    );
  });
});

describe("MessageList", () => {
  beforeEach(() => {
    useInnerLifeStore.setState({ current: null, loading: false, error: null });
  });

  it("渲染传入的 messages，自动滚动不崩", () => {
    render(
      <MessageList
        messages={[
          makeMsg("speak", "nyx", "第一条"),
          makeMsg("message", "user", "第二条"),
        ]}
      />,
    );
    expect(screen.getByText("第一条")).toBeInTheDocument();
    expect(screen.getByText("第二条")).toBeInTheDocument();
  });
});

describe("ChatPanel", () => {
  beforeEach(() => {
    useInnerLifeStore.setState({ current: null, loading: false, error: null });
    useChatStore.getState().reset();
  });

  it("订阅 messages 并透传给 MessageList 渲染", () => {
    useChatStore.setState({ messages: [makeMsg("speak", "nyx", "订阅上屏")] });
    render(<ChatPanel />);
    expect(screen.getByText("订阅上屏")).toBeInTheDocument();
  });
});

describe("ChatInput", () => {
  beforeEach(() => {
    useChatStore.getState().reset();
  });

  it("点发送 → 触发 sendMessage(trimmed) 且成功清空", async () => {
    const sendSpy = vi
      .spyOn(useChatStore.getState(), "sendMessage")
      .mockResolvedValue(true);
    render(<ChatInput />);
    const input = screen.getByPlaceholderText("对 Nyx 说…");
    fireEvent.change(input, { target: { value: "  你好  " } });
    fireEvent.click(screen.getByRole("button", { name: "发送" }));
    expect(sendSpy).toHaveBeenCalledWith("你好");
    await waitFor(() => expect(input).toHaveValue("")); // 成功才清空（flush .then 微任务）
  });

  it("回车 → 触发 sendMessage 且成功清空", async () => {
    const sendSpy = vi
      .spyOn(useChatStore.getState(), "sendMessage")
      .mockResolvedValue(true);
    render(<ChatInput />);
    const input = screen.getByPlaceholderText("对 Nyx 说…");
    fireEvent.change(input, { target: { value: "hi" } });
    fireEvent.keyDown(input, { key: "Enter" });
    expect(sendSpy).toHaveBeenCalledWith("hi");
    await waitFor(() => expect(input).toHaveValue(""));
  });

  it("输入法组合态回车（isComposing）→ 不触发 sendMessage", () => {
    const sendSpy = vi
      .spyOn(useChatStore.getState(), "sendMessage")
      .mockResolvedValue(true);
    render(<ChatInput />);
    const input = screen.getByPlaceholderText("对 Nyx 说…");
    fireEvent.change(input, { target: { value: "nihao" } });
    fireEvent.keyDown(input, { key: "Enter", isComposing: true }); // 拼音选字回车，非真实发送
    expect(sendSpy).not.toHaveBeenCalled();
  });

  it("发送失败（sendMessage 返回 false）→ 输入框保留文本可重试", () => {
    vi.spyOn(useChatStore.getState(), "sendMessage").mockResolvedValue(false);
    render(<ChatInput />);
    const input = screen.getByPlaceholderText("对 Nyx 说…");
    fireEvent.change(input, { target: { value: "你好" } });
    fireEvent.click(screen.getByRole("button", { name: "发送" }));
    expect(input).toHaveValue("你好"); // 失败不清空，用户可重试
  });

  it("isReplying=true → 发送按钮禁用 + 回车不触发", () => {
    useChatStore.setState({ isReplying: true });
    const sendSpy = vi
      .spyOn(useChatStore.getState(), "sendMessage")
      .mockResolvedValue(true);
    render(<ChatInput />);
    const input = screen.getByPlaceholderText("对 Nyx 说…");
    fireEvent.change(input, { target: { value: "想发但被锁" } }); // 填非空，确保只有 isReplying 守卫能拦
    const btn = screen.getByRole("button");
    expect(btn).toBeDisabled();
    expect(btn).toHaveTextContent("…");
    fireEvent.keyDown(input, { key: "Enter" });
    expect(sendSpy).not.toHaveBeenCalled();
  });

  it("sendError 非 null → 红字显示", () => {
    useChatStore.setState({ sendError: "校验失败" });
    render(<ChatInput />);
    expect(screen.getByText("校验失败")).toHaveClass("chat-input__error");
  });
});
