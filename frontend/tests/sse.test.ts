import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { BASE_URL } from "../src/api/client";
import { dispatchEvent } from "../src/api/dispatch";
import { useSSE } from "../src/hooks/useSSE";
import { useChatStore } from "../src/stores/chatStore";
import { useEventStore } from "../src/stores/eventStore";
import { useInnerLifeStore } from "../src/stores/innerLifeStore";
import type { CurrentState } from "../src/types/api";

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
    useEventStore.getState().clear();
    useInnerLifeStore.setState({ current: null, loading: false, error: null });
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

  it("emotion_update → innerLifeStore（覆盖三字段）", () => {
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
  });

  it("未消费类型（reflection）→ eventStore 兜底", () => {
    dispatchEvent({
      event: "reflection",
      event_id: "e3",
      correlation_id: "c3",
      content: "…",
    });

    expect(useEventStore.getState().count).toBe(1);
    expect(useEventStore.getState().events[0].event).toBe("reflection");
  });
});
