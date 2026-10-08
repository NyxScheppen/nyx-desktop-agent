import { create } from "zustand";
import { getEvalPrompt, getEvalRecent, getEvalTotalTokens } from "../api/client";
import type { EvalRecord, EvalStats, LlmPromptMessage } from "../types/api";

const EVAL_PAGE_SIZE = 20;

// eval 记账页面：所有历史 LLM 调用分页加载 + 总 token。
// 页面 mount 触发 refresh，拉 REST 快照；无 SSE 事件驱动（低频面板）。
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

export const useEvalStore = create<EvalStoreState>((set, get) => {
  let requestGeneration = 0;
  return {
  records: null,
  stats: null,
  error: null,
  prompts: {},
  promptLoading: {},
  promptErrors: {},
  refresh: async () => {
    const request = ++requestGeneration;
    set({
      error: null,
      prompts: {},
      promptLoading: {},
      promptErrors: {},
    });
    try {
      const records: EvalRecord[] = [];
      let offset = 0;
      while (true) {
        const page = await getEvalRecent(EVAL_PAGE_SIZE, offset);
        if (request !== requestGeneration) return;
        records.push(...page);
        if (page.length < EVAL_PAGE_SIZE) break;
        offset += EVAL_PAGE_SIZE;
      }
      const stats = await getEvalTotalTokens();
      if (request !== requestGeneration) return;
      set({
        records,
        stats,
        prompts: {},
        promptLoading: {},
        promptErrors: {},
      });
    } catch (err) {
      if (request === requestGeneration) set({
        error: err instanceof Error ? err.message : String(err),
      });
    }
  },
  loadPrompt: async (recordId: string) => {
    const request = requestGeneration;
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
      if (request !== requestGeneration || !get().records?.some((record) => record.id === recordId)) return;
      set((state) => ({
        prompts: { ...state.prompts, [recordId]: prompt },
        promptLoading: { ...state.promptLoading, [recordId]: false },
      }));
    } catch (err) {
      if (request !== requestGeneration || !get().records?.some((record) => record.id === recordId)) return;
      set((state) => ({
        promptLoading: { ...state.promptLoading, [recordId]: false },
        promptErrors: {
          ...state.promptErrors,
          [recordId]: err instanceof Error ? err.message : String(err),
        },
      }));
    }
  },
  };
});
