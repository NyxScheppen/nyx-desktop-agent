import { create } from "zustand";
import { chooseEncounter, getCurrentEncounter } from "../api/client";
import { useChatStore } from "./chatStore";
import { useDesireStore } from "./desireStore";
import { useInnerLifeStore } from "./innerLifeStore";
import { useMemoryStore } from "./memoryStore";
import type {
  EncounterCurrent,
  EncounterEndEvent,
  EncounterStartEvent,
} from "../types/api";

// 遭遇（19-encounter）：ENCOUNTER_START 置位 current（EncounterCard 渲染），
// 用户选选项 POST /api/encounter/choose，ENCOUNTER_END 清位 + ending 上聊天
// 时间线 + 后果改属性（重拉内在/欲望/记忆快照）。
// SSE 主通道：choose 只 POST，不本地清 current（信任 encounter_end 随后到达）。
type EncounterState = {
  current: EncounterCurrent | null; // GET /api/encounter/current 或 encounter_start 置位；null = 无未决遭遇
  choosing: boolean;                // 选项点击后 POST 往返期间禁用（防连击）
  error: string | null;
  onStart: (e: EncounterStartEvent) => void;
  onEnd: (e: EncounterEndEvent) => void;
  choose: (encounterId: string, optionIndex: number) => Promise<void>;
  refresh: () => Promise<void>;     // 进页面恢复未决遭遇
  reset: () => void;
};

export const useEncounterStore = create<EncounterState>((set, get) => ({
  current: null,
  choosing: false,
  error: null,
  onStart: (e) => {
    set({
      current: {
        encounter_id: e.encounter_id,
        kind: e.kind,
        text: e.text,
        options: e.options,
      },
      choosing: false,
      error: null,
    });
  },
  onEnd: (e) => {
    set({ current: null, choosing: false });
    // 结局叙事上聊天时间线（kind:"encounter"）+ 后果改属性 → 重拉快照
    useChatStore.getState().addEncounterEnding(e);
    void useInnerLifeStore.getState().refreshState(); // energy/emotion 变了
    void useDesireStore.getState().refresh();         // 欲望值变了
    void useMemoryStore.getState().refresh();         // 成长时刻落记忆
  },
  choose: async (encounterId, optionIndex) => {
    const cur = get().current;
    if (cur === null || cur.encounter_id !== encounterId) return;
    set({ choosing: true, error: null });
    try {
      await chooseEncounter(encounterId, optionIndex);
      set({ choosing: false });
      // encounter_end SSE 随后清 current + 上屏 ending；此处不提前清（SSE 主通道）
    } catch (err) {
      set({
        choosing: false,
        error: err instanceof Error ? err.message : String(err),
      });
    }
  },
  refresh: async () => {
    set({ error: null });
    try {
      const current = await getCurrentEncounter();
      set({ current, choosing: false });
    } catch (err) {
      set({ error: err instanceof Error ? err.message : String(err) });
    }
  },
  reset: () => set({ current: null, choosing: false, error: null }),
}));
