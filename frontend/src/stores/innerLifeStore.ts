import { create } from "zustand";
import { getState } from "../api/client";
import { isEmotionCategory } from "../types/api";
import type { CurrentState, EmotionUpdateEvent } from "../types/api";

type InnerLifeState = {
  current: CurrentState | null; // GET /api/state 快照；null = 尚未加载
  error: string | null;
  refreshState: () => Promise<void>;
  updateEmotion: (e: EmotionUpdateEvent) => void;
};

export const useInnerLifeStore = create<InnerLifeState>((set) => ({
  current: null,
  error: null,
  refreshState: async () => {
    set({ error: null });
    try {
      const current = await getState();
      set({ current });
    } catch (err) {
      set({
        error: err instanceof Error ? err.message : String(err),
      });
    }
  },
  updateEmotion: (e) => {
    const { valence, arousal, emotion } = e;
    if (
      typeof valence !== "number" ||
      typeof arousal !== "number" ||
      !isEmotionCategory(emotion)
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
