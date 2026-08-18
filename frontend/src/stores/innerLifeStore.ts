import { create } from "zustand";
import type { CurrentState, SseEvent } from "../types/api";

type InnerLifeState = {
  current: CurrentState | null; // GET /api/state 快照；null = 尚未加载
  loading: boolean;
  error: string | null;
  refreshState: () => Promise<void>;
  updateEmotion: (e: SseEvent) => void;
};

// 占位：getState 快照 + emotion_update 增量覆盖在 02-stores 实现阶段填充
export const useInnerLifeStore = create<InnerLifeState>(() => ({
  current: null,
  loading: false,
  error: null,
  refreshState: async () => {},
  updateEmotion: () => {},
}));
