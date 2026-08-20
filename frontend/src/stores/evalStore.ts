import { create } from "zustand";
import { getEval, getTokens } from "../api/client";
import type { EvalReport, TokenUsage } from "../types/api";

// eval + token 看板（README §5）：无对应 SSE 事件，仅挂载/手动刷新（面板内「刷新」按钮）。
type EvalStoreState = {
  reports: EvalReport[] | null;
  tokens: TokenUsage[] | null;
  loading: boolean;
  error: string | null;
  refresh: () => Promise<void>;
};

export const useEvalStore = create<EvalStoreState>((set) => ({
  reports: null,
  tokens: null,
  loading: false,
  error: null,
  refresh: async () => {
    set({ loading: true, error: null });
    try {
      const [reports, tokens] = await Promise.all([getEval(), getTokens()]);
      set({ reports, tokens, loading: false });
    } catch (err) {
      set({
        error: err instanceof Error ? err.message : String(err),
        loading: false,
      });
    }
  },
}));
