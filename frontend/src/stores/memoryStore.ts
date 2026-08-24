import { create } from "zustand";
import { getMemories } from "../api/client";
import type { Memory } from "../types/api";

// 记忆浏览器面板（README §5）：REST 快照 + SSE memory_* 事件触发 refresh。
type MemoryStoreState = {
  data: Memory[] | null;
  error: string | null;
  refresh: () => Promise<void>;
};

export const useMemoryStore = create<MemoryStoreState>((set) => ({
  data: null,
  error: null,
  refresh: async () => {
    set({ error: null });
    try {
      const data = await getMemories();
      set({ data });
    } catch (err) {
      set({
        error: err instanceof Error ? err.message : String(err),
      });
    }
  },
}));
