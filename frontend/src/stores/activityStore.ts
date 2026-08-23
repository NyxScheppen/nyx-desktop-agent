import { create } from "zustand";
import { getActivity, getActivityResults } from "../api/client";
import type { Activity, ActivitySnapshot } from "../types/api";

// 活动时间线面板（README §5）：REST 快照 + SSE activity_* 事件触发 refresh。
// 「产出」面板数据同源：results 与 data 一起并发拉取（evalStore 双字段先例）。
type ActivityStoreState = {
  data: ActivitySnapshot | null;
  results: Activity[] | null;
  loading: boolean;
  error: string | null;
  refresh: () => Promise<void>;
};

export const useActivityStore = create<ActivityStoreState>((set) => ({
  data: null,
  results: null,
  loading: false,
  error: null,
  refresh: async () => {
    set({ loading: true, error: null });
    try {
      const [data, results] = await Promise.all([
        getActivity(),
        getActivityResults(),
      ]);
      set({ data, results, loading: false });
    } catch (err) {
      set({
        error: err instanceof Error ? err.message : String(err),
        loading: false,
      });
    }
  },
}));
