import { captureScope, isScopeCurrent, scopedSetter } from "../api/characterScope";
import { create } from "zustand";
import { getExpressionHistory } from "../api/client";
import type { ExpressionHistory } from "../types/api";

type ExpressionHistoryState = {
  data: ExpressionHistory | null;
  error: string | null;
  refresh: () => Promise<void>;
};

export const useExpressionHistoryStore = create<ExpressionHistoryState>((set) => ({
  data: null,
  error: null,
  refresh: async () => {
    const scope = captureScope();
    const commit = scopedSetter(set);
    try {
      const data = await getExpressionHistory();
      if (isScopeCurrent(scope)) commit({ data, error: null });
    } catch (error) {
      if (isScopeCurrent(scope)) {
        commit({ error: error instanceof Error ? error.message : String(error) });
      }
    }
  },
}));
