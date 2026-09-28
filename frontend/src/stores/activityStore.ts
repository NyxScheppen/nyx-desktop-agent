import { create } from "zustand";
import {
  createBookTask,
  createWebTask,
  getActivity,
  getActivityResults,
  getTasks,
} from "../api/client";
import type { Activity, ActivitySnapshot, AssignedTask } from "../types/api";

// 活动时间线面板：REST 快照 + SSE activity_* 事件触发 refresh。
// 「产出」与「创作」页数据同源：results 只保存按结束时间倒序的创作活动。
const RESULTS_PAGE_SIZE = 12;
let resultsGeneration = 0;

type ActivityStoreState = {
  data: ActivitySnapshot | null;
  results: Activity[] | null;
  tasks: AssignedTask[] | null;
  error: string | null;
  taskError: string | null;
  creatingTask: boolean;
  resultsLoading: boolean;
  hasMoreResults: boolean;
  refresh: () => Promise<void>;
  loadMoreResults: () => Promise<void>;
  createWebTask: (url: string) => Promise<boolean>;
  createBookTask: (bookId: string, targetParagraph: number) => Promise<boolean>;
};

function withTask(tasks: AssignedTask[] | null, task: AssignedTask): AssignedTask[] {
  return [task, ...(tasks ?? []).filter((item) => item.id !== task.id)].sort(
    (left, right) => right.created_at - left.created_at,
  );
}

export const useActivityStore = create<ActivityStoreState>((set, get) => ({
  data: null,
  results: null,
  tasks: null,
  error: null,
  taskError: null,
  creatingTask: false,
  resultsLoading: false,
  hasMoreResults: false,
  refresh: async () => {
    const generation = ++resultsGeneration;
    set({ error: null, resultsLoading: true });
    try {
      const [data, results, tasks] = await Promise.all([
        getActivity(),
        getActivityResults({
          limit: RESULTS_PAGE_SIZE + 1,
          offset: 0,
          activity_type: "creation",
        }),
        getTasks(),
      ]);
      if (generation !== resultsGeneration) return;
      set({
        data,
        results: results.slice(0, RESULTS_PAGE_SIZE),
        tasks,
        hasMoreResults: results.length > RESULTS_PAGE_SIZE,
        resultsLoading: false,
      });
    } catch (err) {
      if (generation !== resultsGeneration) return;
      set({
        error: err instanceof Error ? err.message : String(err),
        resultsLoading: false,
      });
    }
  },
  loadMoreResults: async () => {
    const { results, resultsLoading, hasMoreResults } = get();
    if (results === null || resultsLoading || !hasMoreResults) return;
    const generation = resultsGeneration;
    set({ resultsLoading: true, error: null });
    try {
      const next = await getActivityResults({
        limit: RESULTS_PAGE_SIZE + 1,
        offset: results.length,
        activity_type: "creation",
      });
      if (generation !== resultsGeneration || get().results !== results) return;
      set({
        results: [...results, ...next.slice(0, RESULTS_PAGE_SIZE)],
        hasMoreResults: next.length > RESULTS_PAGE_SIZE,
        resultsLoading: false,
      });
    } catch (err) {
      if (generation !== resultsGeneration || get().results !== results) return;
      set({
        error: err instanceof Error ? err.message : String(err),
        resultsLoading: false,
      });
    }
  },
  createWebTask: async (url) => {
    set({ creatingTask: true, taskError: null });
    try {
      const task = await createWebTask(url);
      set({ tasks: withTask(get().tasks, task), creatingTask: false });
      return true;
    } catch (err) {
      set({
        taskError: err instanceof Error ? err.message : String(err),
        creatingTask: false,
      });
      return false;
    }
  },
  createBookTask: async (bookId, targetParagraph) => {
    set({ creatingTask: true, taskError: null });
    try {
      const task = await createBookTask(bookId, targetParagraph);
      set({ tasks: withTask(get().tasks, task), creatingTask: false });
      return true;
    } catch (err) {
      set({
        taskError: err instanceof Error ? err.message : String(err),
        creatingTask: false,
      });
      return false;
    }
  },
}));
