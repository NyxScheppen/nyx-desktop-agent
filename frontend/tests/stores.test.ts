import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useChatStore } from "../src/stores/chatStore";
import { useEventStore } from "../src/stores/eventStore";
import { useInnerLifeStore } from "../src/stores/innerLifeStore";
import type { CurrentState } from "../src/types/api";

function jsonResponse(body: unknown, ok = true, status = 200): Response {
  return { ok, status, json: async () => body } as Response;
}

const currentFixture: CurrentState = {
  valence: 0.1,
  arousal: 0.2,
  emotion: "neutral",
  personality: {
    openness: 8,
    conscientiousness: 8,
    extraversion: 2,
    agreeableness: 6,
    neuroticism: 7,
  },
  values: {
    attitude_to_human: 8,
    ai_identity_acceptance: 6,
    altruism: 9,
    optimism: 5,
  },
  energy: 100,
  energy_state: "energetic",
  current_activity: null,
  active_desires: [],
};

function resetChat() {
  useChatStore.getState().reset(); // 复用 reset()：全清 messages/isReplying/sendError + 清 module 级 pendingId/replyTimer
}

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("chatStore.add*", () => {
  beforeEach(resetChat);

  it("addSpeak → ChatMessage{role:nyx, kind:speak, content, correlation_id}", () => {
    useChatStore.getState().addSpeak({
      event: "speak",
      event_id: "e1",
      correlation_id: "c1",
      content: "你好",
    });

    const { messages } = useChatStore.getState();
    expect(messages).toHaveLength(1);
    expect(messages[0]).toMatchObject({
      id: "e1",
      role: "nyx",
      kind: "speak",
      content: "你好",
      correlation_id: "c1",
    });
  });

  it("addAsk → kind=ask", () => {
    useChatStore.getState().addAsk({
      event: "ask",
      event_id: "e2",
      correlation_id: "c2",
      content: "想聊聊吗？",
    });
    expect(useChatStore.getState().messages[0]).toMatchObject({
      role: "nyx",
      kind: "ask",
      content: "想聊聊吗？",
    });
  });

  it("addThink → kind=think", () => {
    useChatStore.getState().addThink({
      event: "think",
      event_id: "e3",
      correlation_id: "c3",
      content: "…",
    });
    expect(useChatStore.getState().messages[0]).toMatchObject({
      role: "nyx",
      kind: "think",
    });
  });

  it("addMutter → kind=mutter", () => {
    useChatStore.getState().addMutter({
      event: "mutter",
      event_id: "e4",
      correlation_id: "c4",
      content: "碎碎念",
    });
    expect(useChatStore.getState().messages[0]).toMatchObject({
      role: "nyx",
      kind: "mutter",
    });
  });

  it("addInitiateChat → kind=initiate_chat", () => {
    useChatStore.getState().addInitiateChat({
      event: "initiate_chat",
      event_id: "e5",
      correlation_id: "c5",
      content: "在忙吗？",
    });
    expect(useChatStore.getState().messages[0]).toMatchObject({
      role: "nyx",
      kind: "initiate_chat",
    });
  });

  it("addUserMessage → 读 message（非 content）→ role:user kind:message", () => {
    useChatStore.getState().addUserMessage({
      event: "user_message",
      event_id: "e6",
      correlation_id: "c6",
      message: "你好",
    });
    expect(useChatStore.getState().messages[0]).toMatchObject({
      role: "user",
      kind: "message",
      content: "你好",
    });
  });
});

describe("chatStore.sendMessage", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    resetChat();
  });

  it("成功：POST /api/chat + isReplying=true + sendError=null", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ event_id: "e1" }));
    vi.stubGlobal("fetch", fetchMock);

    await useChatStore.getState().sendMessage("你好");

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/chat");
    expect(init).toMatchObject({
      method: "POST",
      body: JSON.stringify({ message: "你好" }),
    });
    expect(useChatStore.getState().isReplying).toBe(true);
    expect(useChatStore.getState().sendError).toBeNull();
  });

  it("重入守卫：锁在 await 前同步上，in-flight 第二次 sendMessage 不发起第二次 postChat", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ event_id: "e1" }));
    vi.stubGlobal("fetch", fetchMock);

    const first = useChatStore.getState().sendMessage("a"); // 同步置 isReplying=true 后进入 await
    expect(useChatStore.getState().isReplying).toBe(true); // 锁已同步生效（非 React 异步订阅）
    await useChatStore.getState().sendMessage("b"); // 被 get().isReplying 守卫拦下

    expect(fetchMock).toHaveBeenCalledTimes(1);
    await first; // 第一次正常 settle，避免挂起
  });

  it("失败：postChat throw → sendError=e.message，isReplying 仍 false", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(jsonResponse({ detail: "校验失败" }, false, 422)),
    );

    await useChatStore.getState().sendMessage("你好");

    expect(useChatStore.getState().sendError).toBe("校验失败");
    expect(useChatStore.getState().isReplying).toBe(false);
  });

  it("60s 超时：advanceTimersByTime(60_000) → sendError=回复超时 + isReplying=false", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse({ event_id: "e1" })));

    await useChatStore.getState().sendMessage("hi");
    expect(useChatStore.getState().isReplying).toBe(true);

    vi.advanceTimersByTime(60_000);

    expect(useChatStore.getState().isReplying).toBe(false);
    expect(useChatStore.getState().sendError).toBe("回复超时");
  });

  it("addSpeak 取消超时：advanceTimersByTime(60_000) 不触发", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse({ event_id: "e1" })));

    await useChatStore.getState().sendMessage("hi");
    useChatStore.getState().addSpeak({
      event: "speak",
      event_id: "e2",
      correlation_id: "e1",
      content: "回复",
    });
    expect(useChatStore.getState().isReplying).toBe(false);

    vi.advanceTimersByTime(60_000);

    expect(useChatStore.getState().sendError).toBeNull();
  });

  it("迟到回复：超时后 addSpeak 清 sendError", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse({ event_id: "e1" })));

    await useChatStore.getState().sendMessage("hi");
    vi.advanceTimersByTime(60_000);
    expect(useChatStore.getState().sendError).toBe("回复超时");

    useChatStore.getState().addSpeak({
      event: "speak",
      event_id: "e2",
      correlation_id: "e1",
      content: "迟到的回复",
    });

    expect(useChatStore.getState().sendError).toBeNull();
  });

  it("非匹配 correlation 的 speak 不清 timer（isReplying 保持 true）", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse({ event_id: "e1" })));

    await useChatStore.getState().sendMessage("hi");
    expect(useChatStore.getState().isReplying).toBe(true);

    useChatStore.getState().addSpeak({
      event: "speak",
      event_id: "eX",
      correlation_id: "other", // 非本次发送的回复（如搭话）
      content: "别的发言",
    });

    expect(useChatStore.getState().isReplying).toBe(true); // 未误清
    expect(useChatStore.getState().messages).toHaveLength(1); // 但消息照常上屏

    vi.advanceTimersByTime(60_000);
    expect(useChatStore.getState().sendError).toBe("回复超时"); // timer 未被误清
  });
});

describe("chatStore.reset", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    resetChat();
  });

  it("新会话全清：复位 isReplying/sendError + 取消残留 timer", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse({ event_id: "e1" })));

    await useChatStore.getState().sendMessage("hi");
    expect(useChatStore.getState().isReplying).toBe(true);

    useChatStore.getState().reset();

    expect(useChatStore.getState().messages).toHaveLength(0);
    expect(useChatStore.getState().isReplying).toBe(false);
    expect(useChatStore.getState().sendError).toBeNull();

    vi.advanceTimersByTime(60_000); // 残留 timer 已取消，不触发超时
    expect(useChatStore.getState().sendError).toBeNull();
  });
});

describe("innerLifeStore", () => {
  beforeEach(() => {
    useInnerLifeStore.setState({ current: null, loading: false, error: null });
  });

  it("refreshState：loading 状态机 + current 被设置", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(currentFixture));
    vi.stubGlobal("fetch", fetchMock);

    const p = useInnerLifeStore.getState().refreshState();
    expect(useInnerLifeStore.getState().loading).toBe(true); // 同步置 true

    await p;

    expect(fetchMock.mock.calls[0][0]).toBe("/api/state");
    expect(useInnerLifeStore.getState().current).toEqual(currentFixture);
    expect(useInnerLifeStore.getState().loading).toBe(false);
    expect(useInnerLifeStore.getState().error).toBeNull();
  });

  it("refreshState：getState throw → error + loading=false", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockRejectedValue(new TypeError("fetch failed")),
    );

    await useInnerLifeStore.getState().refreshState();

    expect(useInnerLifeStore.getState().error).toBe("fetch failed");
    expect(useInnerLifeStore.getState().loading).toBe(false);
  });

  it("updateEmotion：只覆盖三字段，其余不变", () => {
    useInnerLifeStore.setState({ current: currentFixture });

    useInnerLifeStore.getState().updateEmotion({
      event: "emotion_update",
      event_id: "e1",
      correlation_id: "c1",
      valence: 0.9,
      arousal: 0.8,
      emotion: "happy",
    });

    const cur = useInnerLifeStore.getState().current;
    expect(cur?.valence).toBe(0.9);
    expect(cur?.arousal).toBe(0.8);
    expect(cur?.emotion).toBe("happy");
    expect(cur?.personality).toEqual(currentFixture.personality);
    expect(cur?.energy).toBe(currentFixture.energy);
  });

  it("updateEmotion：current=null 时不崩（忽略）", () => {
    useInnerLifeStore.setState({ current: null });

    expect(() =>
      useInnerLifeStore.getState().updateEmotion({
        event: "emotion_update",
        event_id: "e1",
        correlation_id: "c1",
        valence: 0.9,
        arousal: 0.8,
        emotion: "happy",
      }),
    ).not.toThrow();
    expect(useInnerLifeStore.getState().current).toBeNull();
  });
});

describe("eventStore", () => {
  beforeEach(() => {
    useEventStore.getState().clear();
  });

  it("record：unshift 最新在前 + count++", () => {
    useEventStore.getState().record({
      event: "clock_tick",
      event_id: "e1",
      correlation_id: "c1",
    });
    useEventStore.getState().record({
      event: "reflection",
      event_id: "e2",
      correlation_id: "c2",
    });

    const { events, count } = useEventStore.getState();
    expect(count).toBe(2);
    expect(events).toHaveLength(2);
    expect(events[0].event_id).toBe("e2"); // 最新在前
    expect(events[1].event_id).toBe("e1");
  });

  it("超 MAX_EVENTS(500)：丢最旧，count 累计", () => {
    for (let i = 0; i < 501; i++) {
      useEventStore.getState().record({
        event: "clock_tick",
        event_id: `e${i}`,
        correlation_id: "c",
      });
    }

    const { events, count } = useEventStore.getState();
    expect(events).toHaveLength(500);
    expect(count).toBe(501);
    expect(events[0].event_id).toBe("e500");
    expect(events[499].event_id).toBe("e1"); // e0 被丢
  });
});
