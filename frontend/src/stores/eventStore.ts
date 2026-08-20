import { create } from "zustand";
import type { BackendEvent, SseEvent } from "../types/api";

const MAX_EVENTS = 500; // 内存上限：超出丢最旧但 count 累计（防长时间运行无界增长）

type EventRecord = SseEvent & { received_at: number };

type EventState = {
  events: EventRecord[]; // 时间线，最新在前
  count: number; // 收到事件总数（含被 cap 截断的；只计 SSE 实时，不计 loadHistory）
  record: (e: SseEvent) => void;
  loadHistory: (events: BackendEvent[]) => void; // 溯源面板挂载时回填 GET /api/events/log
  clear: () => void;
};

// BackendEvent（GET /api/events/log）→ EventRecord（SseEvent 形状 + received_at）：
// content 展开进帧体（与 SSE 帧同构，01-sse §1），timestamp(秒) → received_at(毫秒)。
function toEventRecord(e: BackendEvent): EventRecord {
  return {
    event: e.type,
    event_id: e.id,
    correlation_id: e.correlation_id,
    received_at: e.timestamp * 1000,
    ...e.content,
  } as EventRecord;
}

export const useEventStore = create<EventState>((set) => ({
  events: [],
  count: 0,
  record: (e) => {
    const received_at = Date.now();
    set((s) => {
      const events = [{ ...e, received_at }, ...s.events];
      if (events.length > MAX_EVENTS) events.pop(); // 丢最旧
      return { events, count: s.count + 1 };
    });
  },
  loadHistory: (history) => {
    set((s) => {
      const existing = new Set(s.events.map((e) => e.event_id));
      const incoming = history
        .filter((e) => !existing.has(e.id))
        .map(toEventRecord);
      const merged = [...incoming, ...s.events].sort(
        (a, b) => b.received_at - a.received_at,
      );
      return { events: merged.slice(0, MAX_EVENTS) };
    });
  },
  clear: () => set({ events: [], count: 0 }),
}));
