import { create } from "zustand";
import type { CurrentState, EmotionUpdateEvent } from "../types/api";

type InnerLifeState = {
  current: CurrentState | null; // GET /api/state 快照；null = 尚未加载
  loading: boolean;
  error: string | null;
  refreshState: () => Promise<void>;
  updateEmotion: (e: EmotionUpdateEvent) => void;
};

export const useInnerLifeStore = create<InnerLifeState>((set) => ({
  current: null,
  loading: false,
  error: null,
  // 占位：getState 快照在 02-stores + 05-client 实现
  refreshState: async () => {},
  updateEmotion: (e) => {
    const { valence, arousal, emotion } = e;
    if (
      typeof valence !== "number" ||
      typeof arousal !== "number" ||
      typeof emotion !== "string"
    ) {
      console.error("SSE emotion_update 帧字段类型错误，丢弃", e);
      return;
    }
    set((s) => {
      if (s.current === null) return {}; // 快照未回，忽略（等 refreshState 覆盖）
      return {
        current: {
          ...s.current,
          valence,
          arousal,
          emotion,
        },
      };
    });
  },
}));
