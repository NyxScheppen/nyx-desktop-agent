import { create } from "zustand";
import type { SseEvent } from "../types/api";

const MAX_EVENTS = 500; // 内存上限：超出丢最旧但 count 累计（防长时间运行无界增长）

type EventRecord = SseEvent & { received_at: number };

type EventState = {
  events: EventRecord[]; // 时间线，最新在前
  count: number; // 收到事件总数（含被 cap 截断的）
  record: (e: SseEvent) => void;
  clear: () => void;
};

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
  clear: () => set({ events: [], count: 0 }),
}));
