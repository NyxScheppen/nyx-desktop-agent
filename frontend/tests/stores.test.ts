import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { isReady } from "../src/components/chat/MessageList";
import { ANNOUNCE_DURATION, useAnnounceStore } from "../src/stores/announceStore";
import { useActivityStore } from "../src/stores/activityStore";
import { useChatStore, type ChatMessage } from "../src/stores/chatStore";
import { useDesireStore } from "../src/stores/desireStore";
import { useInnerLifeStore } from "../src/stores/innerLifeStore";
import { useMemoryStore } from "../src/stores/memoryStore";
import { useMutterStore } from "../src/stores/mutterStore";
import { useSettingsStore } from "../src/stores/settingsStore";
import type { BackendEvent, CurrentState } from "../src/types/api";

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

  it("addInitiateChat → unreadProactive=true，clearUnreadProactive 复位", () => {
    useChatStore.getState().addInitiateChat({
      event: "initiate_chat",
      event_id: "e5b",
      correlation_id: "c5b",
      content: "在忙吗？",
    });
    expect(useChatStore.getState().unreadProactive).toBe(true);

    useChatStore.getState().clearUnreadProactive();
    expect(useChatStore.getState().unreadProactive).toBe(false);
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

describe("mutterStore", () => {
  beforeEach(() => {
    useMutterStore.setState({ mutters: [] });
  });

  it("addMutter 追加 {id,text}，reset 清空", () => {
    useMutterStore.getState().addMutter("e1", "在想你");
    useMutterStore.getState().addMutter("e2", "又想你");

    expect(useMutterStore.getState().mutters).toEqual([
      { id: "e1", text: "在想你" },
      { id: "e2", text: "又想你" },
    ]);

    useMutterStore.getState().reset();
    expect(useMutterStore.getState().mutters).toHaveLength(0);
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

  it("reset 复位 unreadProactive", () => {
    useChatStore.getState().addInitiateChat({
      event: "initiate_chat",
      event_id: "e7",
      correlation_id: "c7",
      content: "在吗",
    });
    expect(useChatStore.getState().unreadProactive).toBe(true);

    useChatStore.getState().reset();

    expect(useChatStore.getState().unreadProactive).toBe(false);
  });
});

describe("innerLifeStore", () => {
  beforeEach(() => {
    useInnerLifeStore.setState({ current: null, error: null });
  });

  it("refreshState：current 被设置", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(currentFixture));
    vi.stubGlobal("fetch", fetchMock);

    await useInnerLifeStore.getState().refreshState();

    expect(fetchMock.mock.calls[0][0]).toBe("/api/state");
    expect(useInnerLifeStore.getState().current).toEqual(currentFixture);
    expect(useInnerLifeStore.getState().error).toBeNull();
  });

  it("refreshState：getState throw → error", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockRejectedValue(new TypeError("fetch failed")),
    );

    await useInnerLifeStore.getState().refreshState();

    expect(useInnerLifeStore.getState().error).toBe("fetch failed");
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

describe("desireStore / activityStore", () => {
  beforeEach(() => {
    useDesireStore.setState({ data: null, error: null });
    useActivityStore.setState({ data: null, results: null, error: null });
    useMemoryStore.setState({ data: null, error: null });
  });

  it("desireStore.refresh：GET /api/desires → data 落 store", async () => {
    const fixture = { values: [], short_term: [], long_term: [] };
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(fixture));
    vi.stubGlobal("fetch", fetchMock);

    await useDesireStore.getState().refresh();

    expect(fetchMock.mock.calls[0][0]).toBe("/api/desires");
    expect(useDesireStore.getState().data).toEqual(fixture);
  });

  it("activityStore.refresh：并行 getActivity + getActivityResults → data/results 落 store", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse({ current: null, schedule: [] }))
      .mockResolvedValueOnce(jsonResponse([]));
    vi.stubGlobal("fetch", fetchMock);

    await useActivityStore.getState().refresh();

    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(fetchMock.mock.calls[0][0]).toBe("/api/activity");
    expect(fetchMock.mock.calls[1][0]).toBe("/api/activity/results");
    expect(useActivityStore.getState().data).toEqual({ current: null, schedule: [] });
    expect(useActivityStore.getState().results).toEqual([]);
  });

  it("desireStore.refresh：getDesires throw → error", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("fetch failed")));

    await useDesireStore.getState().refresh();

    expect(useDesireStore.getState().error).toBe("fetch failed");
    expect(useDesireStore.getState().data).toBeNull();
  });

  it("memoryStore.refresh：GET /api/memories → data 落 store", async () => {
    const fixture = [
      {
        id: "m1",
        created_at: 1,
        content: "用户喜欢咖啡",
        tag: "user",
        summary: "喜欢咖啡",
        freshness: 1,
        type: "long_term",
        recall_count: 0,
      },
    ];
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(fixture));
    vi.stubGlobal("fetch", fetchMock);

    await useMemoryStore.getState().refresh();

    expect(fetchMock.mock.calls[0][0]).toBe("/api/memories");
    expect(useMemoryStore.getState().data).toEqual(fixture);
  });

  it("memoryStore.refresh：getMemories throw → error", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("fetch failed")));

    await useMemoryStore.getState().refresh();

    expect(useMemoryStore.getState().error).toBe("fetch failed");
    expect(useMemoryStore.getState().data).toBeNull();
  });
});

describe("chatStore.loadHistory", () => {
  beforeEach(() => {
    useChatStore.getState().reset();
  });

  function historyFetch(byType: Record<string, BackendEvent[]>) {
    return vi.fn().mockImplementation((url: string) => {
      const m = /event_type=([a-z_]+)/.exec(url);
      return Promise.resolve(jsonResponse(byType[m?.[1] ?? ""] ?? []));
    });
  }

  it("按 timestamp 升序前置 + preloaded + 历史 think 入 typedIds", async () => {
    vi.stubGlobal(
      "fetch",
      historyFetch({
        user_message: [
          { id: "u1", timestamp: 1000, source: "external", type: "user_message", content: { message: "你好" }, correlation_id: "u1" },
        ],
        think: [
          { id: "t1", timestamp: 1001, source: "internal", type: "think", content: { content: "我想想" }, correlation_id: "u1" },
        ],
        speak: [
          { id: "s1", timestamp: 1002, source: "internal", type: "speak", content: { content: "你好呀" }, correlation_id: "u1" },
        ],
      }),
    );

    await useChatStore.getState().loadHistory();

    const { messages, typedIds } = useChatStore.getState();
    expect(messages.map((m) => m.id)).toEqual(["u1", "t1", "s1"]);
    expect(messages.map((m) => m.kind)).toEqual(["message", "think", "speak"]);
    expect(messages.every((m) => m.preloaded === true)).toBe(true);
    expect(typedIds["t1"]).toBe(true);
  });

  it("已存在的 id 去重，不重复前置", async () => {
    useChatStore.getState().addSpeak({
      event: "speak",
      event_id: "s1",
      correlation_id: "u1",
      content: "你好呀",
    });
    vi.stubGlobal(
      "fetch",
      historyFetch({
        user_message: [
          { id: "u1", timestamp: 1000, source: "external", type: "user_message", content: { message: "你好" }, correlation_id: "u1" },
        ],
        think: [
          { id: "t1", timestamp: 1001, source: "internal", type: "think", content: { content: "我想想" }, correlation_id: "u1" },
        ],
        speak: [
          { id: "s1", timestamp: 1002, source: "internal", type: "speak", content: { content: "你好呀" }, correlation_id: "u1" },
        ],
      }),
    );

    await useChatStore.getState().loadHistory();

    const { messages } = useChatStore.getState();
    expect(messages.map((m) => m.id)).toEqual(["u1", "t1", "s1"]); // s1 不重复
    expect(messages.filter((m) => m.id === "s1")).toHaveLength(1);
  });

  it("getEventsLog 失败：best-effort 不抛、消息不变", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("fetch failed")));

    await expect(useChatStore.getState().loadHistory()).resolves.toBeUndefined();
    expect(useChatStore.getState().messages).toHaveLength(0);
  });

  it("markTyped 标记 + reset 清 typedIds", () => {
    useChatStore.getState().markTyped("x");
    expect(useChatStore.getState().typedIds["x"]).toBe(true);

    useChatStore.getState().reset();
    expect(useChatStore.getState().typedIds).toEqual({});
  });
});

describe("settingsStore", () => {
  beforeEach(() => {
    useSettingsStore.getState().reset();
  });

  it("setTint / setImage 独立落 store，可并存", () => {
    useSettingsStore.getState().setTint("#1f2740");
    useSettingsStore.getState().setImage("data:image/png;base64,xxx");

    expect(useSettingsStore.getState().tint).toBe("#1f2740");
    expect(useSettingsStore.getState().image).toBe("data:image/png;base64,xxx");
  });

  it("reset 恢复默认（tint/image 均 null）", () => {
    useSettingsStore.getState().setTint("#1f2740");
    useSettingsStore.getState().setImage("data:image/png;base64,xxx");

    useSettingsStore.getState().reset();

    expect(useSettingsStore.getState().tint).toBeNull();
    expect(useSettingsStore.getState().image).toBeNull();
  });
});

describe("isReady（串行逐字纯函数）", () => {
  const think: ChatMessage = { id: "t1", role: "nyx", kind: "think", content: "…", correlation_id: "c1" };
  const speak: ChatMessage = { id: "s1", role: "nyx", kind: "speak", content: "你好", correlation_id: "c1" };
  const user: ChatMessage = { id: "u1", role: "user", kind: "message", content: "hi", correlation_id: "c1" };

  it("前置 think 未打完 → speak 等（ready=false）", () => {
    expect(isReady(speak, 1, [think, speak], {})).toBe(false);
  });

  it("前置 think 打完 → speak 就绪（ready=true）", () => {
    expect(isReady(speak, 1, [think, speak], { t1: true })).toBe(true);
  });

  it("无前置 nyx 文本 → 直接就绪", () => {
    expect(isReady(speak, 0, [speak], {})).toBe(true);
    expect(isReady(think, 0, [think], {})).toBe(true);
  });

  it("preloaded nyx 文本直接就绪（不逐字）", () => {
    const pre = { ...speak, preloaded: true };
    expect(isReady(pre, 1, [think, pre], {})).toBe(true);
  });

  it("user 消息恒就绪（不受前置 nyx 文本阻塞）", () => {
    expect(isReady(user, 1, [think, user], {})).toBe(true);
  });

  it("不同 correlation_id 的 nyx 文本不阻塞", () => {
    const other = { ...think, id: "t2", correlation_id: "other" };
    expect(isReady(speak, 1, [other, speak], {})).toBe(true);
  });

  it("think 也受串行门控：等前置同 correlation_id 的 speak 打完", () => {
    const think2 = { ...think, id: "t2" };
    expect(isReady(think2, 1, [speak, think2], {})).toBe(false);
    expect(isReady(think2, 1, [speak, think2], { s1: true })).toBe(true);
  });
});

describe("announceStore", () => {
  beforeEach(() => {
    useAnnounceStore.setState({ items: [] });
  });

  it("announce 追加临时气泡（kind/text 落 store、id 唯一）", () => {
    useAnnounceStore.getState().announce("mutter", "在想你");
    useAnnounceStore.getState().announce("activity", "读完啦");

    const { items } = useAnnounceStore.getState();
    expect(items).toHaveLength(2);
    expect(items[0]).toMatchObject({ kind: "mutter", text: "在想你" });
    expect(items[1]).toMatchObject({ kind: "activity", text: "读完啦" });
    expect(items[0].id).not.toBe(items[1].id);
  });

  it("dismiss 摘除指定 id，其余保留", () => {
    useAnnounceStore.getState().announce("mutter", "a");
    useAnnounceStore.getState().announce("mutter", "b");
    const [first] = useAnnounceStore.getState().items;

    useAnnounceStore.getState().dismiss(first.id);

    const { items } = useAnnounceStore.getState();
    expect(items).toHaveLength(1);
    expect(items[0].text).toBe("b");
  });

  it("到时自动 dismiss（按 kind 时长）", () => {
    vi.useFakeTimers();
    useAnnounceStore.getState().announce("mutter", "在想你");

    vi.advanceTimersByTime(ANNOUNCE_DURATION.mutter);

    expect(useAnnounceStore.getState().items).toHaveLength(0);
  });
});

