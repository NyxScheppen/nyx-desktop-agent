import { create } from "zustand";
import { getNarrative } from "../api/client";
import type { SelfNarrative } from "../types/api";

// 自我叙事面板：REST 快照。反思完成（reflection_done）时刷新 + 高亮新故事（highlightedStory）。
type NarrativeStoreState = {
  data: SelfNarrative | null;
  error: string | null;
  highlightedStory: string | null;
  setHighlightedStory: (story: string) => void;
  refresh: () => Promise<void>;
};

export const useNarrativeStore = create<NarrativeStoreState>((set) => ({
  data: null,
  error: null,
  highlightedStory: null,
  setHighlightedStory: (story) => set({ highlightedStory: story }),
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
