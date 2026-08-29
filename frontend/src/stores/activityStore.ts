import { create } from "zustand";
import { getActivity, getActivityResults } from "../api/client";
import type { Activity, ActivitySnapshot } from "../types/api";

// 活动时间线面板：REST 快照 + SSE activity_* 事件触发 refresh。
// 「产出」面板数据同源：results 与 data 一起并发拉取。
type ActivityStoreState = {
  data: ActivitySnapshot | null;
  results: Activity[] | null;
  error: string | null;
  refresh: () => Promise<void>;
};

export const useActivityStore = create<ActivityStoreState>((set) => ({
  data: null,
  results: null,
  error: null,
  refresh: async () => {
    set({ error: null });
    try {
      const [data, results] = await Promise.all([
        getActivity(),
        getActivityResults(),
      ]);
      set({ data, results });
    } catch (err) {
      set({
        error: err instanceof Error ? err.message : String(err),
      });
    }
  },
}));
