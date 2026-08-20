import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { isFirstTypewriter, isReady } from "../src/components/chat/MessageList";
import { useActivityStore } from "../src/stores/activityStore";
import { useChatStore, type ChatMessage } from "../src/stores/chatStore";
import { useDesireStore } from "../src/stores/desireStore";
import { useEvalStore } from "../src/stores/evalStore";
import { useEventStore } from "../src/stores/eventStore";
import { useInnerLifeStore } from "../src/stores/innerLifeStore";
import { useMaterialsStore } from "../src/stores/materialsStore";
import { useMemoryStore } from "../src/stores/memoryStore";
import { useNarrativeStore } from "../src/stores/narrativeStore";
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

  it("loadHistory：回填历史 + 与现有按 received_at 降序去重", () => {
    useEventStore.getState().record({
      event: "speak",
      event_id: "live-1",
      correlation_id: "c1",
      content: "hi",
    });
    const history: BackendEvent[] = [
      {
        id: "h-1",
        timestamp: 1000,
        source: "internal",
        type: "think",
        content: { content: "…" },
        correlation_id: "c1",
      },
      {
        id: "live-1", // 与现有重复，应被去重
        timestamp: 1001,
        source: "internal",
        type: "speak",
        content: { content: "hi" },
        correlation_id: "c1",
      },
    ];

    useEventStore.getState().loadHistory(history);

    const { events } = useEventStore.getState();
    // 只新增 h-1（live-1 去重）；h-1 在 live-1 之前（timestamp 更小）
    expect(events.map((e) => e.event_id)).toEqual(["live-1", "h-1"]);
  });
});

describe("desireStore / activityStore / memoryStore / evalStore", () => {
  beforeEach(() => {
    useDesireStore.setState({ data: null, loading: false, error: null });
    useActivityStore.setState({ data: null, loading: false, error: null });
    useMemoryStore.setState({ data: null, loading: false, error: null });
    useEvalStore.setState({ reports: null, tokens: null, loading: false, error: null });
  });

  it("desireStore.refresh：GET /api/desires → data 落 store", async () => {
    const fixture = { values: [], short_term: [], long_term: [] };
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(fixture));
    vi.stubGlobal("fetch", fetchMock);

    await useDesireStore.getState().refresh();

    expect(fetchMock.mock.calls[0][0]).toBe("/api/desires");
    expect(useDesireStore.getState().data).toEqual(fixture);
    expect(useDesireStore.getState().loading).toBe(false);
  });

  it("activityStore.refresh：GET /api/activity → data 落 store", async () => {
    const fixture = { current: null, schedule: [] };
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(fixture));
    vi.stubGlobal("fetch", fetchMock);

    await useActivityStore.getState().refresh();

    expect(fetchMock.mock.calls[0][0]).toBe("/api/activity");
    expect(useActivityStore.getState().data).toEqual(fixture);
  });

  it("memoryStore.refresh：GET /api/memories → data 落 store", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse([]));
    vi.stubGlobal("fetch", fetchMock);

    await useMemoryStore.getState().refresh();

    expect(fetchMock.mock.calls[0][0]).toBe("/api/memories");
    expect(useMemoryStore.getState().data).toEqual([]);
  });

  it("evalStore.refresh：并行 getEval + getTokens → reports/tokens 落 store", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse([]));
    vi.stubGlobal("fetch", fetchMock);

    await useEvalStore.getState().refresh();

    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(useEvalStore.getState().reports).toEqual([]);
    expect(useEvalStore.getState().tokens).toEqual([]);
  });

  it("desireStore.refresh：getDesires throw → error + loading=false", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("fetch failed")));

    await useDesireStore.getState().refresh();

    expect(useDesireStore.getState().error).toBe("fetch failed");
    expect(useDesireStore.getState().loading).toBe(false);
    expect(useDesireStore.getState().data).toBeNull();
  });
});

describe("narrativeStore / materialsStore", () => {
  beforeEach(() => {
    useNarrativeStore.setState({ data: null, loading: false, error: null });
    useMaterialsStore.setState({ files: null, uploading: false, error: null });
  });

  it("narrativeStore.refresh：GET /api/narrative → data 落 store", async () => {
    const fixture = {
      identity: "我",
      story: [],
      self_view: {},
      becoming: [],
      updated_at: 123,
    };
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(fixture));
    vi.stubGlobal("fetch", fetchMock);

    await useNarrativeStore.getState().refresh();

    expect(fetchMock.mock.calls[0][0]).toBe("/api/narrative");
    expect(useNarrativeStore.getState().data).toEqual(fixture);
    expect(useNarrativeStore.getState().loading).toBe(false);
  });

  it("materialsStore.refresh：GET /api/materials → files 落 store", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ files: ["a.txt"] }));
    vi.stubGlobal("fetch", fetchMock);

    await useMaterialsStore.getState().refresh();

    expect(fetchMock.mock.calls[0][0]).toBe("/api/materials");
    expect(useMaterialsStore.getState().files).toEqual(["a.txt"]);
  });

  it("materialsStore.upload：POST /api/upload 后重拉 files + uploading 复位", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        jsonResponse({ event_id: "e1", filename: "book.txt", path: "p" }),
      ) // uploadFile
      .mockResolvedValueOnce(jsonResponse({ files: ["book.txt"] })); // getMaterials
    vi.stubGlobal("fetch", fetchMock);

    await useMaterialsStore.getState().upload(
      new File(["内容"], "book.txt", { type: "text/plain" }),
    );

    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(fetchMock.mock.calls[0][0]).toBe("/api/upload");
    expect(fetchMock.mock.calls[1][0]).toBe("/api/materials");
    expect(useMaterialsStore.getState().files).toEqual(["book.txt"]);
    expect(useMaterialsStore.getState().uploading).toBe(false);
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

  it("think 未打完 → speak 等（ready=false）", () => {
    expect(isReady(speak, 1, [think, speak], {})).toBe(false);
  });

  it("think 打完 → speak 就绪（ready=true）", () => {
    expect(isReady(speak, 1, [think, speak], { t1: true })).toBe(true);
  });

  it("无前置 think → speak 直接就绪", () => {
    expect(isReady(speak, 0, [speak], {})).toBe(true);
  });

  it("preloaded speak 直接就绪（不逐字）", () => {
    const pre = { ...speak, preloaded: true };
    expect(isReady(pre, 1, [think, pre], {})).toBe(true);
  });

  it("think 等非 speak/ask 恒就绪", () => {
    expect(isReady(think, 0, [think], {})).toBe(true);
  });

  it("不同 correlation_id 的 think 不阻塞 speak", () => {
    const other = { ...think, id: "t2", correlation_id: "other" };
    expect(isReady(speak, 1, [other, speak], {})).toBe(true);
  });
});

describe("isFirstTypewriter（开头打字机纯函数）", () => {
  const speak1: ChatMessage = { id: "s1", role: "nyx", kind: "speak", content: "你好", correlation_id: "c1" };
  const speak2: ChatMessage = { id: "s2", role: "nyx", kind: "speak", content: "再见", correlation_id: "c2" };
  const user: ChatMessage = { id: "u1", role: "user", kind: "message", content: "hi", correlation_id: "c3" };

  it("第一条 nyx 文本 → true（开头打字机）", () => {
    expect(isFirstTypewriter(0, [speak1, speak2])).toBe(true);
  });

  it("第二条 nyx 文本 → false（后续即时显示）", () => {
    expect(isFirstTypewriter(1, [speak1, speak2])).toBe(false);
  });

  it("user 消息不是候选，跳过", () => {
    expect(isFirstTypewriter(1, [user, speak1])).toBe(true);
  });

  it("preloaded 历史消息跳过，其后第一条实时消息才打字机", () => {
    const hist = { ...speak1, id: "h1", preloaded: true };
    expect(isFirstTypewriter(1, [hist, speak2])).toBe(true);
  });

  it("无 nyx 文本消息 → false", () => {
    expect(isFirstTypewriter(0, [user])).toBe(false);
  });
});
