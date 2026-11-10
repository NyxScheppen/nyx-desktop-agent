import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { bindScope } from "../src/api/characterScope";
import { dispatchEvent, synchronizeAfterReplay } from "../src/api/dispatch";
import { useSSE } from "../src/hooks/useSSE";
import { useBehaviorStore } from "../src/stores/behaviorStore";
import { useActivityStore } from "../src/stores/activityStore";
import { useAnnounceStore } from "../src/stores/announceStore";
import { useChatStore } from "../src/stores/chatStore";
import { useDesireStore } from "../src/stores/desireStore";
import { useInnerLifeStore } from "../src/stores/innerLifeStore";
import { useMemoryStore } from "../src/stores/memoryStore";
import { useReaderStore } from "../src/stores/readerStore";
import { isEmotionCategory, parseSseEvent, parseChatPending } from "../src/types/api";
import type {
  ActivitySnapshot,
  CurrentState,
  ReadingAssociationEvent,
  ReadingMutterEvent,
  ReadingProgressEvent,
  ReadingQuestionEvent,
} from "../src/types/api";

let controller: ReadableStreamDefaultController<Uint8Array>;

describe("reply coverage protocol", () => {
  it.each([
    { reply_complete: false, reply_to_event_ids: ["u"] },
    { reply_complete: true }, { reply_to_event_ids: ["u"] },
    { reply_complete: true, reply_to_event_ids: [] },
    { reply_complete: true, reply_to_event_ids: ["u", "u"] },
    { reply_complete: true, reply_to_event_ids: ["other"] },
  ])("rejects malformed paired fields before dispatch", (coverage) => {
    expect(parseSseEvent("speak", { event_id: "s", correlation_id: "u", timestamp: 1, content: "done", ...coverage })).toBeNull();
  });
  it("rejects a damaged snapshot as a whole", () => {
    expect(parseChatPending({ server_now: 1, truncated: false, items: [
      { event_id: "u", timestamp: 1, status: "pending" },
      { event_id: "u", timestamp: 1, status: "processing" },
    ] })).toBeNull();
    expect(parseChatPending({ server_now: 1, truncated: "false", items: [] })).toBeNull();
  });
});
let signal: AbortSignal;
const dispatch = vi.fn();
const emit = (type: string, data: unknown) => controller.enqueue(new TextEncoder().encode(
  `event: ${type}\ndata: ${typeof data === "string" ? data : JSON.stringify(data)}\n\n`,
));
beforeEach(() => {
  dispatch.mockClear();
  vi.stubGlobal("fetch", vi.fn(async (_url: string, init: RequestInit) => {
    signal = init.signal as AbortSignal;
    return new Response(new ReadableStream<Uint8Array>({
      start(next) { controller = next; },
    }), { headers: { "Content-Type": "text/event-stream" } });
  }));
});
afterEach(() => {
  cleanup();
  bindScope(null);
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("useSSE", () => {
  it("1000 historical invalidations synchronize once at the boundary, then dispatch live", async () => {
    const snapshots = [
      vi.spyOn(useInnerLifeStore.getState(), "refreshState").mockResolvedValue(),
      vi.spyOn(useMemoryStore.getState(), "refresh").mockResolvedValue(),
      vi.spyOn(useDesireStore.getState(), "refresh").mockResolvedValue(),
      vi.spyOn(useActivityStore.getState(), "refresh").mockResolvedValue(),
      vi.spyOn(useChatStore.getState(), "loadHistory").mockResolvedValue(),
      vi.spyOn(useBehaviorStore.getState(), "refresh").mockResolvedValue(),
    ];
    const announce = vi.spyOn(useAnnounceStore.getState(), "announce");
    renderHook(() => useSSE(dispatchEvent, undefined, synchronizeAfterReplay));
    await act(async () => {
      for (let i = 0; i < 1000; i++) emit("memory_created", {
        event_id: `old${i}`, correlation_id: "c", timestamp: 1, memory_id: "m",
      });
      emit("mutter", { event_id: "old-mutter", correlation_id: "c", timestamp: 1, content: "old" });
    });
    for (const snapshot of snapshots) expect(snapshot).not.toHaveBeenCalled();
    expect(announce).not.toHaveBeenCalled();
    await act(async () => {
      emit("replay_complete", { event_seq: 1001 });
      emit("replay_complete", { event_seq: 1001 });
    });
    for (const snapshot of snapshots) expect(snapshot).toHaveBeenCalledTimes(1);
    await act(async () => {
      emit("memory_created", { event_id: "new", correlation_id: "c", timestamp: 2, memory_id: "m" });
    });
    expect(snapshots[1]).toHaveBeenCalledTimes(2);
  });

  it("empty replay synchronizes and an old role's boundary cannot synchronize a new role", async () => {
    const synchronize = vi.fn();
    bindScope({ id: "a", revision: 1 });
    const view = renderHook(({ id, generation }) => useSSE(dispatch, { id, generation }, synchronize),
      { initialProps: { id: "a", generation: 1 } });
    await act(async () => { emit("replay_complete", { character_id: "a", event_seq: 0 }); });
    expect(synchronize).toHaveBeenCalledTimes(1);
    const old = controller;
    bindScope({ id: "b", revision: 2 });
    view.rerender({ id: "b", generation: 2 });
    await act(async () => {
      // Old reader is cancelled; its queued control frame cannot drive new work.
      try { old.enqueue(new TextEncoder().encode('event: replay_complete\ndata: {"character_id":"a","event_seq":0}\n\n')); } catch { /* cancelled */ }
      emit("replay_complete", { character_id: "a", event_seq: 0 });
      emit("replay_complete", { character_id: "b", event_seq: 0 });
    });
    expect(synchronize).toHaveBeenCalledTimes(2);
  });

  it("opens a stream, deduplicates named frames and cancels on unmount", async () => {
    const view = renderHook(() => useSSE(dispatch));
    expect(view.result.current).toBe("connecting");
    await act(async () => {
      const frame = { event_id: "a", correlation_id: "c", timestamp: 123.5, content: "你好" };
      emit("speak", frame); emit("speak", frame);
    });
    expect(view.result.current).toBe("open");
    expect(dispatch.mock.calls.map(([event]) => event)).toEqual([
      { event: "speak", event_id: "a", correlation_id: "c", timestamp: 123.5, content: "你好" },
    ]);
    view.unmount();
    expect(signal.aborted).toBe(true);
  });

  it("rejects invalid data and timestamps while continuing the stream", async () => {
    renderHook(() => useSSE(dispatch));
    const error = vi.spyOn(console, "error").mockImplementation(() => {});
    await act(async () => {
      emit("speak", "not-json");
      for (const timestamp of [undefined, null, "x", 1e308]) {
        emit("speak", { event_id: "bad", correlation_id: "c", timestamp, content: "x" });
      }
      emit("speak", { event_id: "ok", correlation_id: "c", timestamp: 1, content: "ok" });
    });
    expect(dispatch).toHaveBeenCalledTimes(1);
    expect(dispatch.mock.calls[0][0].event_id).toBe("ok");
    expect(error).toHaveBeenCalled();
  });

  it("keeps annotation receipts and all reading event types", async () => {
    renderHook(() => useSSE(dispatch));
    const base = { event_id: "a", correlation_id: "c", timestamp: 1 };
    await act(async () => {
      emit("annotation_feedback", { ...base, reply_event_id: "old", quote: "原文🙂", comment: "反馈", user_event_id: "u" });
      emit("reading_progress", { ...base, event_id: "b", book_id: "book", user_position: 8, nyx_position: 5, reading_speed: 50, read_count: 1, revision: 4 });
      emit("reading_mutter", { ...base, event_id: "c", content: "妙", book_id: "book", paragraph_index: 2 });
      emit("reading_question", { ...base, event_id: "d", content: "?", subtype: "question_reflective", book_id: "book", paragraph_index: 3, selected_text: null });
      emit("reading_association", { ...base, event_id: "e", memory_id: "m", snippet: "片段", book_id: "book", paragraph_index: 4 });
    });
    expect(dispatch.mock.calls.map(([event]) => event.event)).toEqual([
      "annotation_feedback", "reading_progress", "reading_mutter", "reading_question", "reading_association",
    ]);
  });

  it("closes old role streams and ignores their late frames", async () => {
    bindScope({ id: "alice", revision: 1 });
    const view = renderHook(({ id, generation }) => useSSE(dispatch, { id, generation }),
      { initialProps: { id: "alice", generation: 1 } });
    await act(async () => {});
    const oldSignal = signal;
    bindScope({ id: "bob", revision: 2 });
    view.rerender({ id: "bob", generation: 2 });
    await act(async () => {
      emit("speak", { character_id: "alice", event_id: "late", correlation_id: "c", timestamp: 1, content: "late" });
      emit("speak", { character_id: "bob", event_id: "new", correlation_id: "c", timestamp: 1, content: "new" });
    });
    expect(oldSignal.aborted).toBe(true);
    expect(dispatch.mock.calls.map(([event]) => event.event_id)).toEqual(["new"]);
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
    useReaderStore.getState().stopCatchup();
  });

  it("speak → chatStore（kind=speak）", () => {
    dispatchEvent({
      event: "speak",
      event_id: "e1",
      correlation_id: "c1",
      timestamp: 1,
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
      timestamp: 1,
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
      inner_document: { topics: [{ section: "identity", id: "self-understanding", title: "自我理解", content: "我正在认识自己。" }], revision: 1, updated_at: 1000 },
      energy: 100,
      energy_state: "energetic",
      current_activity: null,
      active_desires: [],
    };
    useInnerLifeStore.setState({ current });
    // emotion_update 载荷只带情绪，精力等运行状态不随帧下发；
    // dispatch 现顺带 refreshState() 重拉全量，spy 拦下真实 fetch。
    const refreshSpy = vi
      .spyOn(useInnerLifeStore.getState(), "refreshState")
      .mockResolvedValue(undefined);

    dispatchEvent({
      event: "emotion_update",
      event_id: "e2",
      correlation_id: "c2",
      timestamp: 1,
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
      timestamp: 1,
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
      timestamp: 1,
      activity_id: "z",
    });

    expect(spy).toHaveBeenCalledTimes(1);
  });

  it("task_updated → activityStore.refresh()", () => {
    const spy = vi
      .spyOn(useActivityStore.getState(), "refresh")
      .mockResolvedValue(undefined);

    dispatchEvent({
      event: "task_updated",
      event_id: "t1",
      correlation_id: "task-1",
      timestamp: 1,
      task_id: "task-1",
      status: "running",
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
      timestamp: 1,
      memory_id: "x",
    });

    expect(spy).toHaveBeenCalledTimes(1);
  });

  it("mutter → announceStore（冒气泡，不进 chatStore）", () => {
    dispatchEvent({
      event: "mutter",
      event_id: "m1",
      correlation_id: "c1",
      timestamp: 1,
      content: "在想你",
    });

    expect(useAnnounceStore.getState().items).toHaveLength(1);
    expect(useAnnounceStore.getState().items[0]).toMatchObject({
      kind: "mutter",
      text: "在想你",
    });
    expect(useChatStore.getState().messages).toHaveLength(0);
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
      timestamp: 1,
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

  it.each([true, false])("reflection_done（document_changed=%s）→ 刷新文档、欲望和记录，不生成气泡", (changed) => {
    const desireSpy = vi
      .spyOn(useDesireStore.getState(), "refresh")
      .mockResolvedValue(undefined);
    const innerLifeSpy = vi
      .spyOn(useInnerLifeStore.getState(), "refreshState")
      .mockResolvedValue(undefined);
    const memorySpy = vi
      .spyOn(useMemoryStore.getState(), "refresh")
      .mockResolvedValue(undefined);

    const event = parseSseEvent("reflection_done", {
      event_id: "e1",
      correlation_id: "c1",
      timestamp: 1,
      summary: "今天对用户了解更多",
      document_changed: changed,
    });
    expect(event).not.toBeNull();
    dispatchEvent(event!);

    expect(desireSpy).toHaveBeenCalledTimes(1);
    expect(innerLifeSpy).toHaveBeenCalledTimes(1);
    expect(memorySpy).toHaveBeenCalledTimes(1);
    expect(useAnnounceStore.getState().items).toHaveLength(0);
  });

  it("reading_question → chatStore.addReadingTurn；reading_association 静默丢弃；reading_mutter → announce(mutter)", () => {
    const addReadingTurnSpy = vi
      .spyOn(useChatStore.getState(), "addReadingTurn")
      .mockImplementation(() => {});

    const mutter: ReadingMutterEvent = {
      event: "reading_mutter",
      event_id: "e1",
      correlation_id: "b1",
      timestamp: 1,
      content: "妙",
      book_id: "b1",
      paragraph_index: 2,
    };
    const question: ReadingQuestionEvent = {
      event: "reading_question",
      event_id: "e2",
      correlation_id: "b1",
      timestamp: 2,
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
      timestamp: 3,
      memory_id: "m1",
      snippet: "片段",
      book_id: "b1",
      paragraph_index: 4,
    };

    dispatchEvent(question);
    dispatchEvent(association);
    dispatchEvent(mutter);

    expect(addReadingTurnSpy).toHaveBeenCalledTimes(1);
    expect(addReadingTurnSpy).toHaveBeenNthCalledWith(1, question);
    expect(useAnnounceStore.getState().items).toHaveLength(1);
    expect(useAnnounceStore.getState().items[0]).toMatchObject({ kind: "mutter", text: "妙" });
  });

  it("reading_progress → readerStore.applyProgressEvent", () => {
    const applySpy = vi
      .spyOn(useReaderStore.getState(), "applyProgressEvent")
      .mockImplementation(() => {});
    const event: ReadingProgressEvent = {
      event: "reading_progress",
      event_id: "p1",
      correlation_id: "b1",
      timestamp: 1,
      book_id: "b1",
      user_position: 8,
      nyx_position: 5,
      reading_speed: 50,
      read_count: 1,
      revision: 4,
    };

    dispatchEvent(event);

    expect(applySpy).toHaveBeenCalledWith(event);
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
