import { captureScope, isScopeCurrent, scopedSetter } from "../api/characterScope";
import { create } from "zustand";
import { getMemories, getMemorySearch, getRecentFacts } from "../api/client";
import type { FactScope, Memory, MemoryFact, MemoryKind } from "../types/api";

// 记忆面板 store：记忆/事实 REST 快照 + SSE memory_* 事件触发 refresh。
type MemoryStoreState = {
  data: Memory[] | null;
  facts: MemoryFact[] | null;
  query: string;
  kind: MemoryKind | null;
  factScope: FactScope;
  error: string | null;
  factsError: string | null;
  refresh: () => Promise<void>;
  search: (query: string, kind?: MemoryKind | null) => Promise<void>;
  selectFactScope: (scope: FactScope) => Promise<void>;
};

function errorMessage(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}

export const useMemoryStore = create<MemoryStoreState>((set, get) => {
  let memoryGeneration = 0;
  let factGeneration = 0;
  return {
    data: null,
    facts: null,
    query: "",
    kind: null,
    factScope: "profile",
    error: null,
    factsError: null,
    refresh: async () => {
      const scope = captureScope();
      const commit = scopedSetter(set);
      const memoryRequestId = ++memoryGeneration;
      const factRequestId = ++factGeneration;
      const query = get().query.trim();
      const kind = get().kind;
      const factScope = get().factScope;
      commit({ error: null, factsError: null });
      const memoryRequest = query === "" ? getMemories(kind) : getMemorySearch(query, kind);
      const [memoryResult, factsResult] = await Promise.allSettled([
        memoryRequest,
        getRecentFacts(64, factScope),
      ]);
      if (!isScopeCurrent(scope)) return;
      if (memoryRequestId === memoryGeneration && get().query === query && get().kind === kind) {
        if (memoryResult.status === "fulfilled") {
          commit({ data: memoryResult.value, error: null });
        } else {
          commit({ error: errorMessage(memoryResult.reason) });
        }
      }
      if (factRequestId === factGeneration && get().factScope === factScope) {
        if (factsResult.status === "fulfilled") {
          commit({ facts: factsResult.value, factsError: null });
        } else {
          commit({ factsError: errorMessage(factsResult.reason) });
        }
      }
    },
    search: async (query: string, kind = get().kind) => {
      const scope = captureScope();
      const commit = scopedSetter(set);
      const normalized = query.trim();
      const request = ++memoryGeneration;
      commit({ query: normalized, kind, data: null, error: null });
      try {
        const data = normalized === "" ? await getMemories(kind) : await getMemorySearch(normalized, kind);
        if (!isScopeCurrent(scope)) return;
        if (request === memoryGeneration && get().query === normalized && get().kind === kind) {
          commit({ data, error: null });
        }
      } catch (err) {
        if (!isScopeCurrent(scope)) return;
        if (request === memoryGeneration && get().query === normalized && get().kind === kind) {
          commit({ error: errorMessage(err) });
        }
      }
    },
    selectFactScope: async (factScope: FactScope) => {
      const scope = captureScope();
      const commit = scopedSetter(set);
      const request = ++factGeneration;
      commit({ factScope, facts: null, factsError: null });
      try {
        const facts = await getRecentFacts(64, factScope);
        if (!isScopeCurrent(scope)) return;
        if (request === factGeneration && get().factScope === factScope) {
          commit({ facts, factsError: null });
        }
      } catch (err) {
        if (!isScopeCurrent(scope)) return;
        if (request === factGeneration && get().factScope === factScope) {
          commit({ factsError: errorMessage(err) });
        }
      }
    },
  };
});
