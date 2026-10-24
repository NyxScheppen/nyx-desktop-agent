import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import ChatInput from "../src/components/chat/ChatInput";
import MessageBubble from "../src/components/chat/MessageBubble";
import MessageList from "../src/components/chat/MessageList";
import { KnowledgeFeed } from "../src/components/chat/KnowledgeFeed";
import { bindScope } from "../src/api/characterScope";
import { useChatStore, type ChatMessage } from "../src/stores/chatStore";

let msgSeq = 0; // 自增保证 id 唯一，避免同 kind 两条消息撞 React key

function makeMsg(
  kind: ChatMessage["kind"],
  role: ChatMessage["role"],
  content: string,
): ChatMessage {
  msgSeq += 1;
  return {
    id: `id-${kind}-${msgSeq}`,
    role,
    kind,
    content,
    correlation_id: "c",
    timestamp: 1,
  };
}

// nyx 文本消息走 useTypewriter 逐字，测试须推进 fake timers 打完再断言完整文案。
// 分多轮 advance，每轮边界让 React flush（打字机 useEffect 在 commit 后推进）。
function typeDone() {
  for (let i = 0; i < 20; i++) {
    act(() => vi.advanceTimersByTime(500));
  }
}

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("KnowledgeFeed", () => {
  afterEach(() => bindScope(null));
  it("显示真实新增数量、失败与重试状态", async () => {
    bindScope({ id: "alice", revision: 1 });
    const fetch = vi.fn()
      .mockResolvedValueOnce({ ok: true, json: async () => ({ created_memories: 1, created_facts: 2, chunks: [] }) })
      .mockResolvedValueOnce({ ok: true, json: async () => ({ created_memories: 0, created_facts: 0, chunks: [{}], errors: ["消化失败"] }) });
    vi.stubGlobal("fetch", fetch);
    render(<KnowledgeFeed />);
    fireEvent.change(screen.getByLabelText("知识文本"), { target: { value: "原始记录" } });
    fireEvent.click(screen.getByText("进知识库"));
    expect(screen.getByText("正在摄入…")).toBeDisabled();
    expect(await screen.findByRole("status")).toHaveTextContent("已进知识库 · 新增 1 条知识、2 条事实");
    fireEvent.change(screen.getByLabelText("知识文本"), { target: { value: "失败原文" } });
    fireEvent.click(screen.getByText("进知识库"));
    expect(await screen.findByRole("alert")).toHaveTextContent("部分摄入未完成");
    expect(screen.getByLabelText("知识文本")).toHaveValue("失败原文");
  });
  it.each([true, false])("角色切换后忽略旧请求的成功或失败: %s", async (ok) => {
    bindScope({ id: "alice", revision: 1 });
    let finish!: (value: unknown) => void;
    vi.stubGlobal("fetch", vi.fn().mockReturnValue(new Promise((resolve) => { finish = resolve; })));
    const view = render(<KnowledgeFeed key="alice" />);
    fireEvent.change(screen.getByLabelText("知识文本"), { target: { value: "Alice 的原文" } });
    fireEvent.click(screen.getByText("进知识库"));
    bindScope({ id: "bob", revision: 2 });
    view.rerender(<KnowledgeFeed key="bob" />);
    await act(async () => finish({ ok, json: async () => ok
      ? { created_memories: 1, created_facts: 1, chunks: [] }
      : { detail: "旧角色失败" } }));
    expect(screen.queryByRole("status")).toBeNull();
    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.getByLabelText("知识文本")).toHaveValue("");
    expect(screen.getByText("进知识库")).toBeDisabled();
  });
});

describe("MessageBubble", () => {
  it("完整 SPEAK 的单条选区保留原文，跨气泡选择拒绝", () => {
    const a = { ...makeMsg("speak", "nyx", "中文🙂 原文"), preloaded: true };
    const b = { ...makeMsg("speak", "nyx", "另一条"), preloaded: true };
    render(<><MessageBubble message={a} ready onTyped={() => {}} /><MessageBubble message={b} ready onTyped={() => {}} /></>);
    const first = screen.getByText(a.content);
    const second = screen.getByText(b.content);
    const range = document.createRange();
    range.setStart(first.firstChild!, 0); range.setEnd(first.firstChild!, 4);
    window.getSelection()!.removeAllRanges(); window.getSelection()!.addRange(range);
    fireEvent.click(first);
    expect(useChatStore.getState().annotationDraft?.quote).toBe("中文🙂");
    useChatStore.getState().cancelAnnotation();
    range.setEnd(second.firstChild!, 2);
    window.getSelection()!.removeAllRanges(); window.getSelection()!.addRange(range);
    fireEvent.click(first);
    expect(useChatStore.getState().annotationDraft).toBeNull();
    window.getSelection()!.removeAllRanges();
  });
  it("打字未完及 ASK 不批注；长全文提示选区而不截断", () => {
    const message = makeMsg("speak", "nyx", "未完成");
    const view = render(<MessageBubble message={message} ready onTyped={() => {}} />);
    fireEvent.click(view.container.querySelector(".message-bubble__content")!);
    expect(useChatStore.getState().annotationDraft).toBeNull();
    view.rerender(<MessageBubble message={{ ...makeMsg("ask", "nyx", "问题"), preloaded: true }} ready onTyped={() => {}} />);
    fireEvent.click(screen.getByText("问题"));
    expect(useChatStore.getState().annotationDraft).toBeNull();
    view.rerender(<MessageBubble message={{ ...makeMsg("speak", "nyx", "🙂".repeat(2001)), preloaded: true }} ready onTyped={() => {}} />);
    fireEvent.click(view.container.querySelector(".message-bubble__content")!);
    expect(useChatStore.getState().annotationDraft).toBeNull();
    expect(useChatStore.getState().sendError).toContain("选择片段");
  });
  beforeEach(() => {
    useChatStore.getState().reset();
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("speak 逐字显示完整回复", () => {
    render(<MessageBubble message={makeMsg("speak", "nyx", "你好")} ready onTyped={() => {}} />);
    typeDone();
    expect(screen.getByText("你好")).toBeInTheDocument();
  });

  it("首次打完后重新挂载同一消息，立即显示全文", () => {
    const message = makeMsg("speak", "nyx", "只播放一次");
    const first = render(<MessageBubble message={message} ready onTyped={() => {}} />);
    expect(screen.queryByText("只播放一次")).not.toBeInTheDocument();
    typeDone();
    first.unmount();

    render(<MessageBubble message={message} ready onTyped={() => {}} />);
    expect(screen.getByText("只播放一次")).toBeInTheDocument();
    expect(document.querySelector(".cursor-blink")).not.toBeInTheDocument();
  });

  it("首次播放中途关闭后重新挂载，立即显示全文并解锁后续消息", () => {
    const message = makeMsg("think", "nyx", "尚未播完的内心话");
    const onTyped = vi.fn();
    const first = render(<MessageBubble message={message} ready onTyped={onTyped} />);
    act(() => vi.advanceTimersByTime(35));
    expect(screen.queryByText("尚未播完的内心话")).not.toBeInTheDocument();
    expect(useChatStore.getState().seenIds[message.id]).toBe(true);
    first.unmount();

    render(<MessageBubble message={message} ready onTyped={onTyped} />);
    expect(screen.getByText("尚未播完的内心话")).toBeInTheDocument();
    expect(onTyped).toHaveBeenCalledWith(message.id);
  });

  it("尚未 ready 的消息不记作已显示，角色 reset 清除已看记录", () => {
    const message = makeMsg("speak", "nyx", "等待前句");
    const waiting = render(<MessageBubble message={message} ready={false} onTyped={() => {}} />);
    expect(useChatStore.getState().seenIds[message.id]).toBeUndefined();
    waiting.unmount();

    const first = render(<MessageBubble message={message} ready onTyped={() => {}} />);
    expect(screen.queryByText("等待前句")).not.toBeInTheDocument();
    expect(useChatStore.getState().seenIds[message.id]).toBe(true);
    first.unmount();
    useChatStore.getState().reset();

    render(<MessageBubble message={message} ready onTyped={() => {}} />);
    expect(screen.queryByText("等待前句")).not.toBeInTheDocument();
  });

  it("think → 逐字弱化显示（不再折叠）", () => {
    render(<MessageBubble message={makeMsg("think", "nyx", "内心独白")} ready onTyped={() => {}} />);
    typeDone();
    const el = screen.getByText("内心独白");
    expect(el).toBeInTheDocument();
  });

  it("initiate_chat → 带「欲望搭话」标记 + 逐字 content", () => {
    render(<MessageBubble message={makeMsg("initiate_chat", "nyx", "在忙吗？")} ready onTyped={() => {}} />);
    expect(screen.getByText("欲望搭话")).toBeInTheDocument(); // 标记即时
    typeDone();
    expect(screen.getByText("在忙吗？")).toBeInTheDocument();
  });

  it("等待中的 initiate_chat 显示服务端校正倒计时", () => {
    vi.setSystemTime(new Date("2026-10-12T08:00:00Z"));
    useChatStore.setState({ serverTimeOffset: 0 });
    render(
      <MessageBubble
        message={{
          ...makeMsg("initiate_chat", "nyx", "在忙吗？"),
          preloaded: true,
          attempt_id: "attempt-chat",
          expires_at: Date.now() / 1000 + 10,
          question_status: "waiting",
        }}
        ready
        onTyped={() => {}}
      />,
    );

    expect(screen.getByText("剩余 10 秒")).toBeInTheDocument();
    act(() => vi.advanceTimersByTime(1000));
    expect(screen.getByText("剩余 9 秒")).toBeInTheDocument();
  });

  it("user message 即时显示完整文本", () => {
    render(<MessageBubble message={makeMsg("message", "user", "你好")} ready onTyped={() => {}} />);
    expect(screen.getByText("你好")).toBeInTheDocument();
  });

  it("reading_question → 「提问」徽标 + 即时全量 + selectedText 引文行", () => {
    render(
      <MessageBubble
        message={{
          id: "q1",
          role: "nyx",
          kind: "reading_question",
          content: "为什么？",
          correlation_id: "b1",
          timestamp: 1,
          subtype: "quote_question",
          selectedText: "划线句",
        }}
        ready
        onTyped={() => {}}
      />,
    );
    // 不逐字：content 即时全量（无需 typeDone）
    expect(screen.getByText("为什么？")).toBeInTheDocument();
    expect(screen.getByText("提问")).toBeInTheDocument();
    expect(screen.getByText("原文：「划线句」")).toBeInTheDocument();
  });
});

describe("MessageList", () => {
  beforeEach(() => {
    useChatStore.getState().reset();
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("首条和跨日消息显示时间，同一回复 think/speak 不重复", () => {
    const yesterday = new Date(2026, 8, 17, 19, 0).getTime() / 1000;
    const today = new Date(2026, 8, 18, 8, 0).getTime() / 1000;
    const messages: ChatMessage[] = [
      { ...makeMsg("message", "user", "我去吃饭了"), timestamp: yesterday, preloaded: true },
      { ...makeMsg("speak", "nyx", "我等你"), timestamp: yesterday + 60, preloaded: true },
      { ...makeMsg("message", "user", "早上好"), correlation_id: "next", timestamp: today, preloaded: true },
      { ...makeMsg("think", "nyx", "回来了"), correlation_id: "next", timestamp: today + 1, preloaded: true },
      { ...makeMsg("speak", "nyx", "早上好"), correlation_id: "next", timestamp: today + 2, preloaded: true },
    ];

    render(<MessageList messages={messages} now={new Date(2026, 8, 18, 8, 1)} />);

    expect(screen.getAllByText("昨天 19:00")).toHaveLength(1);
    expect(screen.getAllByText("今天 08:00")).toHaveLength(1);
    expect(document.querySelectorAll(".message-time-divider")).toHaveLength(2);
  });

  it("全部消息按序渲染，无历史折叠", () => {
    render(
      <MessageList
        messages={[
          makeMsg("speak", "nyx", "第一条"),
          makeMsg("message", "user", "第二条"),
        ]}
      />,
    );
    typeDone();
    // 两条都上屏（微信式：不再只显示一条 / 折叠历史）
    expect(screen.getByText("第一条")).toBeInTheDocument();
    expect(screen.getByText("第二条")).toBeInTheDocument();
    // 无历史按钮
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });

  it("全部气泡渲染即存在（串行门控只延迟内容，不延迟挂载）", () => {
    const { container } = render(
      <MessageList
        messages={[
          makeMsg("speak", "nyx", "第一句"),
          makeMsg("think", "nyx", "内心"),
        ]}
      />,
    );
    // 两条气泡都在 DOM（nyx 打字中 content 渐显，但气泡已挂载）
    expect(container.querySelectorAll(".message-bubble").length).toBe(2);
  });

  it("串行逐字：内心话先打完、对话才开打", () => {
    render(
      <MessageList
        messages={[
          makeMsg("think", "nyx", "内心话"),
          makeMsg("speak", "nyx", "对话话"),
        ]}
      />,
    );
    // 未推进 timer：think 刚开打（空），speak 等前置 think 打完（空）
    expect(screen.queryByText("内心话")).not.toBeInTheDocument();
    expect(screen.queryByText("对话话")).not.toBeInTheDocument();
    typeDone();
    // think 打完 → markTyped → speak 就绪，两条都完整上屏
    expect(screen.getByText("内心话")).toBeInTheDocument();
    expect(screen.getByText("对话话")).toBeInTheDocument();
  });

  it("中途重新打开：已开始的内心话直接完成，未开始的对话仍逐字", () => {
    const messages = [
      makeMsg("think", "nyx", "先说内心话"),
      makeMsg("speak", "nyx", "随后再说话"),
    ];
    const first = render(<MessageList messages={messages} />);
    act(() => vi.advanceTimersByTime(35));
    expect(useChatStore.getState().seenIds[messages[0].id]).toBe(true);
    expect(useChatStore.getState().seenIds[messages[1].id]).toBeUndefined();
    first.unmount();

    render(<MessageList messages={messages} />);
    expect(screen.getByText("先说内心话")).toBeInTheDocument();
    expect(screen.queryByText("随后再说话")).not.toBeInTheDocument();
    typeDone();
    expect(screen.getByText("随后再说话")).toBeInTheDocument();
  });
});

describe("ChatInput", () => {
  beforeEach(() => {
    useChatStore.getState().reset();
  });

  it("选择回复、编辑批注并发送，保留普通草稿且仅在回执后显示已记录", async () => {
    const fetch = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ event_id: "annotation-user", annotation_persistence: "pending" }) });
    vi.stubGlobal("fetch", fetch);
    const message = { ...makeMsg("speak", "nyx", "原文🙂"), preloaded: true };
    render(<><MessageBubble message={message} ready onTyped={() => {}} /><ChatInput /><MessageList messages={useChatStore.getState().messages} /></>);
    const input = screen.getByPlaceholderText("对 Nyx 说…");
    fireEvent.change(input, { target: { value: "普通草稿" } });
    window.getSelection()!.removeAllRanges();
    fireEvent.click(screen.getByText(message.content));
    expect(screen.getByRole("button", { name: "发送批注" })).toBeDisabled();
    fireEvent.change(screen.getByLabelText("对回复的批注"), { target: { value: " 请先说明不确定性🙂 " } });
    const annotation = { reply_event_id: message.id, quote: message.content, comment: " 请先说明不确定性🙂 " };
    fireEvent.click(screen.getByRole("button", { name: "发送批注" }));
    await waitFor(() => expect(screen.queryByLabelText("对回复的批注")).toBeNull());
    expect(JSON.parse(fetch.mock.calls[0][1].body)).toEqual({ message: annotation.comment, discussion: null, annotation });
    expect(input).toHaveValue("普通草稿");
    const user: ChatMessage = { id: "annotation-user", kind: "message", role: "user", content: annotation.comment, annotation, correlation_id: "annotation-user", timestamp: 2 };
    const view = render(<MessageBubble message={user} ready onTyped={() => {}} />);
    expect(screen.getByText("反馈持久化待确认")).toBeInTheDocument();
    act(() => useChatStore.getState().addAnnotationFeedback({ ...annotation, event: "annotation_feedback", event_id: "receipt", user_event_id: user.id, correlation_id: user.id, timestamp: 3 }));
    expect(screen.getByText("反馈已记录")).toBeInTheDocument();
    view.unmount();
    act(() => useChatStore.getState().reset());
  });

  it("讨论模式控件在自动、讨论、陪伴三态间切换", () => {
    render(<ChatInput />);

    expect(useChatStore.getState().discussionMode).toBeNull();
    expect(screen.getByRole("button", { name: "自动" })).toHaveAttribute("aria-pressed", "true");

    fireEvent.click(screen.getByRole("button", { name: "讨论" }));
    expect(useChatStore.getState().discussionMode).toBe(true);

    fireEvent.click(screen.getByRole("button", { name: "陪伴" }));
    expect(useChatStore.getState().discussionMode).toBe(false);

    fireEvent.click(screen.getByRole("button", { name: "自动" }));
    expect(useChatStore.getState().discussionMode).toBeNull();
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
    const btn = screen.getByRole("button", { name: "…" });
    expect(btn).toBeDisabled();
    expect(btn).toHaveTextContent("…");
    fireEvent.keyDown(input, { key: "Enter" });
    expect(sendSpy).not.toHaveBeenCalled();
  });

  it("sendError 非 null → 红字显示", () => {
    useChatStore.setState({ sendError: "校验失败" });
    render(<ChatInput />);
    expect(screen.getByText("校验失败")).toBeInTheDocument();
  });
});
