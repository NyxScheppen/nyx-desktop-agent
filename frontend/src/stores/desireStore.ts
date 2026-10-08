import { create } from "zustand";
import { getDesires } from "../api/client";
import type { DesireState } from "../types/api";

// 欲望面板（README §5）：REST 快照 + SSE desire_* 事件触发 refresh（02-stores 模式）。
type DesireStoreState = {
  data: DesireState | null;
  error: string | null;
  refresh: () => Promise<void>;
};

export const useDesireStore = create<DesireStoreState>((set) => {
  let requestGeneration = 0;
  return {
    data: null,
    error: null,
    refresh: async () => {
      const request = ++requestGeneration;
      set({ error: null });
      try {
        const data = await getDesires();
        if (request === requestGeneration) set({ data });
      } catch (err) {
        if (request === requestGeneration) {
          set({ error: err instanceof Error ? err.message : String(err) });
        }
      }
    },
  };
});
