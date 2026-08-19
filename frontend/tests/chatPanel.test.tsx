import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
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

// nyx 文本消息走 useTypewriter 逐字，测试须推进 fake timers 打完再断言完整文案。
// 串行推进依赖 useEffect（React commit 后才设下一个停顿 timer），
// 故分多轮 advance，每轮边界让 React flush，覆盖「打字 → 停顿 → 下一条」全程。
function typeDone() {
  for (let i = 0; i < 20; i++) {
    act(() => vi.advanceTimersByTime(500));
  }
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("MessageBubble", () => {
  beforeEach(() => {
    useInnerLifeStore.setState({ current: null, loading: false, error: null });
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("speak → 左气泡 class + content 上屏", () => {
    render(<MessageBubble message={makeMsg("speak", "nyx", "你好")} />);
    typeDone();
    expect(screen.getByText("你好")).toHaveClass("message-bubble__content");
    expect(screen.getByText("你好").closest(".message-bubble")).toHaveClass(
      "message-bubble--speak",
    );
  });

  it("ask → 高亮 class", () => {
    render(<MessageBubble message={makeMsg("ask", "nyx", "想聊聊吗？")} />);
    typeDone();
    expect(screen.getByText("想聊聊吗？").closest(".message-bubble")).toHaveClass(
      "message-bubble--ask",
    );
  });

  it("think → 逐字弱化显示（不再折叠）", () => {
    render(<MessageBubble message={makeMsg("think", "nyx", "内心独白")} />);
    typeDone();
    const el = screen.getByText("内心独白");
    expect(el).toBeInTheDocument();
    expect(el.closest(".message-bubble")).toHaveClass("message-bubble--think");
  });

  it("initiate_chat → 带「搭话」标记 + 逐字 content", () => {
    render(<MessageBubble message={makeMsg("initiate_chat", "nyx", "在忙吗？")} />);
    expect(screen.getByText("搭话")).toBeInTheDocument(); // 标记即时
    typeDone();
    expect(screen.getByText("在忙吗？")).toBeInTheDocument();
  });

  it("user message → 右气泡 class（即时，不打字）", () => {
    render(<MessageBubble message={makeMsg("message", "user", "你好")} />);
    expect(screen.getByText("你好").closest(".message-bubble")).toHaveClass(
      "message-bubble--user",
    );
  });
});

describe("MessageList", () => {
  beforeEach(() => {
    useInnerLifeStore.setState({ current: null, loading: false, error: null });
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("只显示当前一条，更早的收进历史可展开", () => {
    render(
      <MessageList
        messages={[
          makeMsg("speak", "nyx", "第一条"),
          makeMsg("message", "user", "第二条"),
        ]}
      />,
    );
    typeDone();

    // 当前显示最后一条（停留），第一条进历史折叠
    expect(screen.getByText("第二条")).toBeInTheDocument();
    expect(screen.queryByText("第一条")).not.toBeInTheDocument();

    // 点开历史看到第一条
    fireEvent.click(screen.getByRole("button", { name: "历史（1）" }));
    expect(screen.getByText("第一条")).toBeInTheDocument();
  });

  it("串行：第一条未打完时，后续不渲染", () => {
    render(
      <MessageList
        messages={[
          makeMsg("speak", "nyx", "第一句话"),
          makeMsg("think", "nyx", "内心"),
        ]}
      />,
    );
    // 未推进 fake timers：只有第一条在打字，第二条不显示
    expect(screen.queryByText("内心")).not.toBeInTheDocument();
  });
});

describe("ChatPanel", () => {
  beforeEach(() => {
    useInnerLifeStore.setState({ current: null, loading: false, error: null });
    useChatStore.getState().reset();
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("订阅 messages 并透传给 MessageList 渲染", () => {
    useChatStore.setState({ messages: [makeMsg("speak", "nyx", "订阅上屏")] });
    render(<ChatPanel />);
    typeDone();
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

  it("成功清空不误删预打文本（回复期间输入已改 → 保留）", async () => {
    let resolveSend!: (v: boolean) => void;
    vi.spyOn(useChatStore.getState(), "sendMessage").mockReturnValue(
      new Promise<boolean>((resolve) => {
        resolveSend = resolve;
      }),
    );
    render(<ChatInput />);
    const input = screen.getByPlaceholderText("对 Nyx 说…");
    fireEvent.change(input, { target: { value: "你好" } });
    fireEvent.click(screen.getByRole("button", { name: "发送" }));
    fireEvent.change(input, { target: { value: "再见" } }); // 发送在途时预打下一句

    await act(async () => {
      resolveSend(true); // 第一次发送成功 → 触发 .then
    });

    expect(input).toHaveValue("再见"); // 预打文本不被清
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
