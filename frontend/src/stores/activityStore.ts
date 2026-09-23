import { create } from "zustand";
import { getActivity, getActivityResults } from "../api/client";
import type { Activity, ActivitySnapshot } from "../types/api";

// 活动时间线面板：REST 快照 + SSE activity_* 事件触发 refresh。
// 「产出」与「创作」页数据同源：results 只保存按结束时间倒序的创作活动。
const RESULTS_PAGE_SIZE = 12;

type ActivityStoreState = {
  data: ActivitySnapshot | null;
  results: Activity[] | null;
  error: string | null;
  resultsLoading: boolean;
  hasMoreResults: boolean;
  refresh: () => Promise<void>;
  loadMoreResults: () => Promise<void>;
};

export const useActivityStore = create<ActivityStoreState>((set, get) => ({
  data: null,
  results: null,
  error: null,
  resultsLoading: false,
  hasMoreResults: false,
  refresh: async () => {
    set({ error: null });
    try {
      const [data, results] = await Promise.all([
        getActivity(),
        getActivityResults({
          limit: RESULTS_PAGE_SIZE + 1,
          offset: 0,
          activity_type: "creation",
        }),
      ]);
      set({
        data,
        results: results.slice(0, RESULTS_PAGE_SIZE),
        hasMoreResults: results.length > RESULTS_PAGE_SIZE,
      });
    } catch (err) {
      set({
        error: err instanceof Error ? err.message : String(err),
      });
    }
  },
  loadMoreResults: async () => {
    const { results, resultsLoading, hasMoreResults } = get();
    if (results === null || resultsLoading || !hasMoreResults) return;
    set({ resultsLoading: true, error: null });
    try {
      const next = await getActivityResults({
        limit: RESULTS_PAGE_SIZE + 1,
        offset: results.length,
        activity_type: "creation",
      });
      set({
        results: [...results, ...next.slice(0, RESULTS_PAGE_SIZE)],
        hasMoreResults: next.length > RESULTS_PAGE_SIZE,
      });
    } catch (err) {
      set({ error: err instanceof Error ? err.message : String(err) });
    } finally {
      set({ resultsLoading: false });
    }
  },
}));
