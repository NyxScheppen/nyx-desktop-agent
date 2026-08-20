import { create } from "zustand";
import { getDesires } from "../api/client";
import type { DesireState } from "../types/api";

// 欲望面板（README §5）：REST 快照 + SSE desire_* 事件触发 refresh（02-stores 模式）。
type DesireStoreState = {
  data: DesireState | null;
  loading: boolean;
  error: string | null;
  refresh: () => Promise<void>;
};

export const useDesireStore = create<DesireStoreState>((set) => ({
  data: null,
  loading: false,
  error: null,
  refresh: async () => {
    set({ loading: true, error: null });
    try {
      const data = await getDesires();
      set({ data, loading: false });
    } catch (err) {
      set({
        error: err instanceof Error ? err.message : String(err),
        loading: false,
      });
    }
  },
}));
