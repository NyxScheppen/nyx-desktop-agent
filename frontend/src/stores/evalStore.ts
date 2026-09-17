import { create } from "zustand";
import { getEvalPrompt, getEvalRecent, getEvalTotalTokens } from "../api/client";
import type { EvalRecord, EvalStats, LlmPromptMessage } from "../types/api";

// eval 记账面板（10-eval）：最近 5 条 LLM 调用 + 总 token。
// 设置弹层打开时 mount 触发 refresh，拉 REST 快照；无 SSE 事件驱动（低频面板）。
type EvalStoreState = {
  records: EvalRecord[] | null;
  stats: EvalStats | null;
  error: string | null;
  prompts: Record<string, LlmPromptMessage[] | null>;
  promptLoading: Record<string, boolean>;
  promptErrors: Record<string, string>;
  refresh: () => Promise<void>;
  loadPrompt: (recordId: string) => Promise<void>;
};

export const useEvalStore = create<EvalStoreState>((set, get) => ({
  records: null,
  stats: null,
  error: null,
  prompts: {},
  promptLoading: {},
  promptErrors: {},
  refresh: async () => {
    set({
      error: null,
      prompts: {},
      promptLoading: {},
      promptErrors: {},
    });
    try {
      const [records, stats] = await Promise.all([
        getEvalRecent(5),
        getEvalTotalTokens(),
      ]);
      set({
        records,
        stats,
        prompts: {},
        promptLoading: {},
        promptErrors: {},
      });
    } catch (err) {
      set({
        error: err instanceof Error ? err.message : String(err),
      });
    }
  },
  loadPrompt: async (recordId: string) => {
    const current = get();
    if (
      Object.hasOwn(current.prompts, recordId)
      || current.promptLoading[recordId]
    ) {
      return;
    }
    set((state) => {
      const promptErrors = { ...state.promptErrors };
      delete promptErrors[recordId];
      return {
        promptLoading: { ...state.promptLoading, [recordId]: true },
        promptErrors,
      };
    });
    try {
      const prompt = await getEvalPrompt(recordId);
      if (!get().records?.some((record) => record.id === recordId)) return;
      set((state) => ({
        prompts: { ...state.prompts, [recordId]: prompt },
        promptLoading: { ...state.promptLoading, [recordId]: false },
      }));
    } catch (err) {
      if (!get().records?.some((record) => record.id === recordId)) return;
      set((state) => ({
        promptLoading: { ...state.promptLoading, [recordId]: false },
        promptErrors: {
          ...state.promptErrors,
          [recordId]: err instanceof Error ? err.message : String(err),
        },
      }));
    }
  },
}));
