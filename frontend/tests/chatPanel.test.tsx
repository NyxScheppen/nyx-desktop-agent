import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import ChatInput from "../src/components/chat/ChatInput";
import ChatPanel from "../src/components/chat/ChatPanel";
import MessageBubble from "../src/components/chat/MessageBubble";
import MessageList from "../src/components/chat/MessageList";
import { useChatStore, type ChatMessage } from "../src/stores/chatStore";
import { useInnerLifeStore } from "../src/stores/innerLifeStore";

function makeMsg(
  kind: ChatMessage["kind"],
  role: ChatMessage["role"],
  content: string,
): ChatMessage {
  return { id: `id-${kind}`, role, kind, content, correlation_id: "c" };
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

  it("点发送 → 触发 sendMessage(trimmed)", () => {
    const sendSpy = vi
      .spyOn(useChatStore.getState(), "sendMessage")
      .mockResolvedValue(undefined);
    render(<ChatInput />);
    fireEvent.change(screen.getByPlaceholderText("对 Nyx 说…"), {
      target: { value: "  你好  " },
    });
    fireEvent.click(screen.getByRole("button", { name: "发送" }));
    expect(sendSpy).toHaveBeenCalledWith("你好");
  });

  it("回车 → 触发 sendMessage", () => {
    const sendSpy = vi
      .spyOn(useChatStore.getState(), "sendMessage")
      .mockResolvedValue(undefined);
    render(<ChatInput />);
    const input = screen.getByPlaceholderText("对 Nyx 说…");
    fireEvent.change(input, { target: { value: "hi" } });
    fireEvent.keyDown(input, { key: "Enter" });
    expect(sendSpy).toHaveBeenCalledWith("hi");
  });

  it("isReplying=true → 发送按钮禁用 + 回车不触发", () => {
    useChatStore.setState({ isReplying: true });
    const sendSpy = vi
      .spyOn(useChatStore.getState(), "sendMessage")
      .mockResolvedValue(undefined);
    render(<ChatInput />);
    const btn = screen.getByRole("button");
    expect(btn).toBeDisabled();
    expect(btn).toHaveTextContent("…");
    fireEvent.keyDown(screen.getByPlaceholderText("对 Nyx 说…"), { key: "Enter" });
    expect(sendSpy).not.toHaveBeenCalled();
  });

  it("sendError 非 null → 红字显示", () => {
    useChatStore.setState({ sendError: "校验失败" });
    render(<ChatInput />);
    expect(screen.getByText("校验失败")).toHaveClass("chat-input__error");
  });
});
