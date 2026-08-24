import { create } from "zustand";
import { getNarrative } from "../api/client";
import type { SelfNarrative } from "../types/api";

// 自我叙事面板：REST 快照（无 SSE 事件，挂载拉取）。对齐 innerLifeStore 的 {data,error,refresh} 形状。
type NarrativeStoreState = {
  data: SelfNarrative | null;
  error: string | null;
  refresh: () => Promise<void>;
};

export const useNarrativeStore = create<NarrativeStoreState>((set) => ({
  data: null,
  error: null,
  refresh: async () => {
    set({ error: null });
    try {
      const data = await getNarrative();
      set({ data });
    } catch (err) {
      set({
        error: err instanceof Error ? err.message : String(err),
      });
    }
  },
}));
