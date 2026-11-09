import { coalescedRefresh } from "../api/coalescedRefresh";
import { captureScope, isScopeCurrent, scopedSetter } from "../api/characterScope";
import { create } from "zustand";
import { getDesires } from "../api/client";
import type { DesireState } from "../types/api";

// 欲望面板：REST 快照 + SSE desire_* 事件触发 refresh。
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
    refresh: coalescedRefresh(async () => {
      const scope = captureScope();
      const commit = scopedSetter(set);
      const request = ++requestGeneration;
      commit({ error: null });
      try {
        const data = await getDesires();
      if (!isScopeCurrent(scope)) return;
        if (request === requestGeneration) commit({ data });
      } catch (err) {
      if (!isScopeCurrent(scope)) return;
        if (request === requestGeneration) {
          commit({ error: err instanceof Error ? err.message : String(err) });
        }
      }
    }),
  };
});
