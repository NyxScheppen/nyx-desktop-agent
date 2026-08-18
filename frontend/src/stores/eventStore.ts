import { create } from "zustand";
import type { SseEvent } from "../types/api";

type EventRecord = SseEvent & { received_at: number };

type EventState = {
  events: EventRecord[]; // 时间线，最新在前
  count: number; // 收到事件总数（含被 cap 截断的）
  record: (e: SseEvent) => void;
  clear: () => void;
};

// 占位：兜底事件时间线 + MAX_EVENTS cap 在 02-stores 实现阶段填充
export const useEventStore = create<EventState>(() => ({
  events: [],
  count: 0,
  record: () => {},
  clear: () => {},
}));
