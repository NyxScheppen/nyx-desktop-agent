import { captureScope, isScopeCurrent, scopedSetter } from "../api/characterScope";
import { create } from "zustand";
import { getMemories, getMemorySearch, getRecentFacts } from "../api/client";
import type { Memory, MemoryFact } from "../types/api";

// 记忆面板 store：记忆/事实 REST 快照 + SSE memory_* 事件触发 refresh。
type MemoryStoreState = {
  data: Memory[] | null;
  facts: MemoryFact[] | null;
  query: string;
  error: string | null;
  factsError: string | null;
  refresh: () => Promise<void>;
  search: (query: string) => Promise<void>;
};

function errorMessage(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}

export const useMemoryStore = create<MemoryStoreState>((set, get) => {
  let requestGeneration = 0;
  return {
    data: null,
    facts: null,
    query: "",
    error: null,
    factsError: null,
    refresh: async () => {
      const scope = captureScope();
      const commit = scopedSetter(set);
      const request = ++requestGeneration;
      const query = get().query.trim();
      commit({ error: null, factsError: null });
      const memoryRequest = query === "" ? getMemories() : getMemorySearch(query);
      const [memoryResult, factsResult] = await Promise.allSettled([
        memoryRequest,
        getRecentFacts(),
      ]);
      if (!isScopeCurrent(scope)) return;
      if (request !== requestGeneration || get().query.trim() !== query) return;
      if (memoryResult.status === "fulfilled") {
        commit({ data: memoryResult.value, error: null });
      } else {
        commit({ error: errorMessage(memoryResult.reason) });
      }
      if (factsResult.status === "fulfilled") {
        commit({ facts: factsResult.value, factsError: null });
      } else {
        commit({ factsError: errorMessage(factsResult.reason) });
      }
    },
    search: async (query: string) => {
      const scope = captureScope();
      const commit = scopedSetter(set);
      const normalized = query.trim();
      const request = ++requestGeneration;
      commit({ query: normalized, error: null });
      try {
        const data = normalized === "" ? await getMemories() : await getMemorySearch(normalized);
      if (!isScopeCurrent(scope)) return;
        if (request === requestGeneration && get().query === normalized) {
          commit({ data, error: null });
        }
      } catch (err) {
      if (!isScopeCurrent(scope)) return;
        if (request === requestGeneration && get().query === normalized) {
          commit({ error: errorMessage(err) });
        }
      }
    },
  };
});
