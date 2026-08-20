import { create } from "zustand";
import { getActivity } from "../api/client";
import type { ActivitySnapshot } from "../types/api";

// 活动时间线面板（README §5）：REST 快照 + SSE activity_* 事件触发 refresh。
type ActivityStoreState = {
  data: ActivitySnapshot | null;
  loading: boolean;
  error: string | null;
  refresh: () => Promise<void>;
};

export const useActivityStore = create<ActivityStoreState>((set) => ({
  data: null,
  loading: false,
  error: null,
  refresh: async () => {
    set({ loading: true, error: null });
    try {
      const data = await getActivity();
      set({ data, loading: false });
    } catch (err) {
      set({
        error: err instanceof Error ? err.message : String(err),
        loading: false,
      });
    }
  },
}));
