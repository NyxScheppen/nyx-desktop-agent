import { create } from "zustand";
import { getEvalRecent, getEvalTotalTokens } from "../api/client";
import type { EvalRecord, EvalStats } from "../types/api";

// eval 记账面板（15-eval）：最近 5 条 LLM 调用 + 总 token。
// 设置弹层打开时 mount 触发 refresh，拉 REST 快照；无 SSE 事件驱动（低频面板）。
type EvalStoreState = {
  records: EvalRecord[] | null;
  stats: EvalStats | null;
  error: string | null;
  refresh: () => Promise<void>;
};

export const useEvalStore = create<EvalStoreState>((set) => ({
  records: null,
  stats: null,
  error: null,
  refresh: async () => {
    set({ error: null });
    try {
      const [records, stats] = await Promise.all([
        getEvalRecent(5),
        getEvalTotalTokens(),
      ]);
      set({ records, stats });
    } catch (err) {
      set({
        error: err instanceof Error ? err.message : String(err),
      });
    }
  },
}));
