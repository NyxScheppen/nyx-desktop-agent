import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { BASE_URL } from "../src/api/client";
import { dispatchEvent } from "../src/api/dispatch";
import { useSSE } from "../src/hooks/useSSE";
import { useActivityStore } from "../src/stores/activityStore";
import { useAnnounceStore } from "../src/stores/announceStore";
import { useChatStore } from "../src/stores/chatStore";
import { useDesireStore } from "../src/stores/desireStore";
import { useInnerLifeStore } from "../src/stores/innerLifeStore";
import { useMemoryStore } from "../src/stores/memoryStore";
import { useMutterStore } from "../src/stores/mutterStore";
import { useReaderStore } from "../src/stores/readerStore";
import { isEmotionCategory } from "../src/types/api";
import type {
  ActivitySnapshot,
  CurrentState,
  ReadingAssociationEvent,
  ReadingMutterEvent,
  ReadingQuestionEvent,
} from "../src/types/api";

// —— fake EventSource（jsdom 无原生实现，需 stub）——
class FakeEventSource {
  static instances: FakeEventSource[] = [];
  url: string;
  onopen: (() => void) | null = null;
  onerror: (() => void) | null = null;
  closed = false;
  private handlers = new Map<string, Array<(e: MessageEvent) => void>>();

  constructor(url: string) {
    this.url = url;
    FakeEventSource.instances.push(this);
  }
  addEventListener(type: string, handler: (e: MessageEvent) => void) {
    const list = this.handlers.get(type) ?? [];
    list.push(handler);
    this.handlers.set(type, list);
  }
  close() {
    this.closed = true;
  }
  emit(type: string, data: string) {
    const ev = { data } as MessageEvent;
    for (const h of this.handlers.get(type) ?? []) h(ev);
  }
}

beforeEach(() => {
  FakeEventSource.instances = [];
  vi.stubGlobal("EventSource", FakeEventSource);
});

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("useSSE", () => {
  it("挂载即 new EventSource(/api/events)，初始 connecting", () => {
    const dispatch = vi.fn();
    const { result } = renderHook(() => useSSE(dispatch));

    expect(FakeEventSource.instances).toHaveLength(1);
    expect(FakeEventSource.instances[0].url).toBe(`${BASE_URL}/api/events`);
    expect(result.current).toBe("connecting");
  });

  it("onopen → open，onerror → connecting", () => {
    const dispatch = vi.fn();
    const { result } = renderHook(() => useSSE(dispatch));
    const source = FakeEventSource.instances[0];

    act(() => source.onopen?.());
    expect(result.current).toBe("open");

    act(() => source.onerror?.());
    expect(result.current).toBe("connecting");
  });

  it("命名帧解析正确并 dispatch", () => {
    const dispatch = vi.fn();
    renderHook(() => useSSE(dispatch));
    const source = FakeEventSource.instances[0];

    act(() => {
      source.emit(
        "speak",
        JSON.stringify({ event_id: "e1", correlation_id: "c1", content: "你好" }),
      );
    });

    expect(dispatch).toHaveBeenCalledWith({
      event: "speak",
      event_id: "e1",
      correlation_id: "c1",
      content: "你好",
    });
  });

  it("EVENT_TYPES 含三型阅读事件：reading_mutter/question/association 帧被监听并 dispatch", () => {
    const dispatch = vi.fn();
    renderHook(() => useSSE(dispatch));
    const source = FakeEventSource.instances[0];

    act(() => {
      source.emit(
        "reading_mutter",
        JSON.stringify({ event_id: "e1", correlation_id: "b1", content: "妙", book_id: "b1", paragraph_index: 2 }),
      );
      source.emit(
        "reading_question",
        JSON.stringify({ event_id: "e2", correlation_id: "b1", content: "?", subtype: "question_reflective", book_id: "b1", paragraph_index: 3, selected_text: null }),
      );
      source.emit(
        "reading_association",
        JSON.stringify({ event_id: "e3", correlation_id: "b1", memory_id: "m1", snippet: "片段", book_id: "b1", paragraph_index: 4 }),
      );
    });

    // 缺任一值则该型帧被浏览器静默丢弃（addEventListener 未注册），dispatch 不会被调。
    expect(dispatch).toHaveBeenCalledTimes(3);
    expect(dispatch).toHaveBeenNthCalledWith(1, expect.objectContaining({ event: "reading_mutter", book_id: "b1" }));
    expect(dispatch).toHaveBeenNthCalledWith(2, expect.objectContaining({ event: "reading_question", subtype: "question_reflective" }));
    expect(dispatch).toHaveBeenNthCalledWith(3, expect.objectContaining({ event: "reading_association", memory_id: "m1" }));
  });

  it("坏 data / 缺字段帧跳过不崩", () => {
    const dispatch = vi.fn();
    renderHook(() => useSSE(dispatch));
    const source = FakeEventSource.instances[0];

    act(() => source.emit("speak", "not-json")); // 非法 JSON
    act(() => source.emit("speak", JSON.stringify({ content: "x" }))); // 缺 event_id/correlation_id
    act(() =>
      source.emit(
        "speak",
        JSON.stringify({ event_id: "e1", correlation_id: "c1", content: "ok" }),
      ),
    );

    expect(dispatch).toHaveBeenCalledTimes(1); // 仅正常帧通过
  });

  it("unmount 调 close()", () => {
    const dispatch = vi.fn();
    const { unmount } = renderHook(() => useSSE(dispatch));
    const source = FakeEventSource.instances[0];

    unmount();
    expect(source.closed).toBe(true);
  });
});

describe("dispatchEvent", () => {
  beforeEach(() => {
    useChatStore.getState().reset();
    useInnerLifeStore.setState({ current: null, error: null });
    useDesireStore.setState({ data: null, error: null });
    useActivityStore.setState({ data: null, error: null });
    useMemoryStore.setState({ data: null, error: null });
    useAnnounceStore.setState({ items: [] });
    useMutterStore.setState({ mutters: [] });
  });

  it("speak → chatStore（kind=speak）", () => {
    dispatchEvent({
      event: "speak",
      event_id: "e1",
      correlation_id: "c1",
      content: "hi",
    });

    const { messages } = useChatStore.getState();
    expect(messages).toHaveLength(1);
    expect(messages[0]).toMatchObject({ kind: "speak", role: "nyx", content: "hi" });
  });

  it("user_message → chatStore（读 message 非 content，回归 Finding 1）", () => {
    dispatchEvent({
      event: "user_message",
      event_id: "e4",
      correlation_id: "c4",
      message: "hi",
    });

    const { messages } = useChatStore.getState();
    expect(messages).toHaveLength(1);
    expect(messages[0]).toMatchObject({ kind: "message", role: "user", content: "hi" });
  });

  it("emotion_update → innerLifeStore（覆盖三字段 + 顺带 refreshState 重拉全量）", () => {
    const current: CurrentState = {
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
    useInnerLifeStore.setState({ current });
    // emotion_update 载荷只带情绪（12-inner-life），能量/性格/三观不随帧下发；
    // dispatch 现顺带 refreshState() 重拉全量，spy 拦下真实 fetch。
    const refreshSpy = vi
      .spyOn(useInnerLifeStore.getState(), "refreshState")
      .mockResolvedValue(undefined);

    dispatchEvent({
      event: "emotion_update",
      event_id: "e2",
      correlation_id: "c2",
      valence: 0.5,
      arousal: 0.3,
      emotion: "happy",
    });

    expect(useInnerLifeStore.getState().current?.valence).toBe(0.5);
    expect(useInnerLifeStore.getState().current?.emotion).toBe("happy");
    expect(refreshSpy).toHaveBeenCalledTimes(1);
  });

  it("desire_generated → desireStore.refresh()", () => {
    const spy = vi
      .spyOn(useDesireStore.getState(), "refresh")
      .mockResolvedValue(undefined);

    dispatchEvent({
      event: "desire_generated",
      event_id: "d1",
      correlation_id: "c1",
      desire_id: "x",
    });

    expect(spy).toHaveBeenCalledTimes(1);
  });

  it("activity_start → activityStore.refresh()", () => {
    const spy = vi
      .spyOn(useActivityStore.getState(), "refresh")
      .mockResolvedValue(undefined);

    dispatchEvent({
      event: "activity_start",
      event_id: "a1",
      correlation_id: "c1",
      activity_id: "z",
    });

    expect(spy).toHaveBeenCalledTimes(1);
  });

  it("memory_created → memoryStore.refresh()", () => {
    const spy = vi
      .spyOn(useMemoryStore.getState(), "refresh")
      .mockResolvedValue(undefined);

    dispatchEvent({
      event: "memory_created",
      event_id: "m1",
      correlation_id: "c1",
      memory_id: "x",
    });

    expect(spy).toHaveBeenCalledTimes(1);
  });

  it("mutter → mutterStore（不进 chatStore、不冒气泡）", () => {
    dispatchEvent({
      event: "mutter",
      event_id: "m1",
      correlation_id: "c1",
      content: "在想你",
    });

    expect(useMutterStore.getState().mutters).toEqual([
      { id: "m1", text: "在想你" },
    ]);
    expect(useChatStore.getState().messages).toHaveLength(0);
    expect(useAnnounceStore.getState().items).toHaveLength(0);
  });

  it("mutter 非 string content → addMutter 丢弃", () => {
    dispatchEvent({
      event: "mutter",
      event_id: "m1",
      correlation_id: "c1",
      content: 123 as unknown as string,
    });

    expect(useMutterStore.getState().mutters).toHaveLength(0);
  });

  it("activity_end → refresh 后按 activity_id 找到产出并 announce", async () => {
    const snap: ActivitySnapshot = {
      current: null,
      schedule: [
        {
          id: "a1",
          type: "reading",
          schedule_block_id: "b1",
          status: "completed",
          progress: { result: { book: "《小王子》", note: "关于驯服" } },
          started_at: 1,
          ended_at: 2,
        },
      ],
    };
    useActivityStore.setState({ data: snap, error: null });
    vi.spyOn(useActivityStore.getState(), "refresh").mockImplementation(async () => {
      useActivityStore.setState({ data: snap });
    });

    dispatchEvent({
      event: "activity_end",
      event_id: "a1",
      correlation_id: "c1",
      activity_id: "a1",
    });

    await waitFor(() => {
      expect(useAnnounceStore.getState().items).toHaveLength(1);
    });
    expect(useAnnounceStore.getState().items[0]).toMatchObject({
      kind: "activity",
      text: "读完啦：《小王子》 — 关于驯服",
    });
  });

  it("reflection_done（story_is_new）→ 欲望 refresh + 气泡", () => {
    const desireSpy = vi
      .spyOn(useDesireStore.getState(), "refresh")
      .mockResolvedValue(undefined);

    dispatchEvent({
      event: "reflection_done",
      event_id: "e1",
      correlation_id: "c1",
      story: "今天对用户了解更多",
      story_is_new: true,
    });

    expect(desireSpy).toHaveBeenCalledTimes(1);
    expect(useAnnounceStore.getState().items).toHaveLength(1);
    expect(useAnnounceStore.getState().items[0]).toMatchObject({
      kind: "mutter",
      text: "小狐狸我呀，反思了一下：今天对用户了解更多",
    });
  });

  it("reflection_done（story_is_new=false）→ 静默 refresh 不气泡", () => {
    const desireSpy = vi
      .spyOn(useDesireStore.getState(), "refresh")
      .mockResolvedValue(undefined);

    dispatchEvent({
      event: "reflection_done",
      event_id: "e2",
      correlation_id: "c2",
      story: "初始故事",
      story_is_new: false,
    });

    expect(desireSpy).toHaveBeenCalledTimes(1);
    expect(useAnnounceStore.getState().items).toHaveLength(0);
  });

  it("reading_mutter/question/association → readerStore.addReadingBubble（透传原始事件）", () => {
    const spy = vi
      .spyOn(useReaderStore.getState(), "addReadingBubble")
      .mockImplementation(() => {});

    const mutter: ReadingMutterEvent = {
      event: "reading_mutter",
      event_id: "e1",
      correlation_id: "b1",
      content: "妙",
      book_id: "b1",
      paragraph_index: 2,
    };
    const question: ReadingQuestionEvent = {
      event: "reading_question",
      event_id: "e2",
      correlation_id: "b1",
      content: "?",
      subtype: "question_reflective",
      book_id: "b1",
      paragraph_index: 3,
      selected_text: null,
    };
    const association: ReadingAssociationEvent = {
      event: "reading_association",
      event_id: "e3",
      correlation_id: "b1",
      memory_id: "m1",
      snippet: "片段",
      book_id: "b1",
      paragraph_index: 4,
    };

    dispatchEvent(mutter);
    dispatchEvent(question);
    dispatchEvent(association);

    expect(spy).toHaveBeenCalledTimes(3);
    expect(spy).toHaveBeenNthCalledWith(1, mutter);
    expect(spy).toHaveBeenNthCalledWith(2, question);
    expect(spy).toHaveBeenNthCalledWith(3, association);
  });
});

describe("isEmotionCategory", () => {
  it("合法枚举 true，非法字符串/非字符串 false", () => {
    expect(isEmotionCategory("happy")).toBe(true);
    expect(isEmotionCategory("neutral")).toBe(true);
    expect(isEmotionCategory("不存在")).toBe(false);
    expect(isEmotionCategory(5)).toBe(false);
    expect(isEmotionCategory(null)).toBe(false);
  });
});
