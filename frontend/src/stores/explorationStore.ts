import { create } from "zustand";
import {
  chooseExploration,
  postExplore,
  setExplorationAutopilot,
} from "../api/client";
import type {
  ExplorationDecision,
  ExplorationStepEvent,
  FloorNode,
} from "../types/api";

// 逐层地牢探索（design §6/§7）：decision = 当前决策载荷（exploration_step 推送），
// 用户 choose 提交决策，新决策经 SSE exploration_step 推回（SSE 主通道，同 encounter）。
// 托管 autopilot：POST /api/explore/autopilot 开关；手动点任意选项 = 随时接管（组件层先关托管再走）。

/** 本地走过的节点（展开地图用）：手动 choose 时记录，托管期间只推进 floor。 */
type VisitRecord = { floor: number; name: string; kind: FloorNode["kind"] };

type ExplorationStoreState = {
  decision: ExplorationDecision | null; // 当前决策载荷；null = 无进行中 run
  activityId: string | null;
  autopilot: boolean;  // 托管开关（本地镜像，POST 后乐观更新）
  choosing: boolean;   // 决策提交中（防连击），新决策到达或终局时复位
  error: string | null;
  history: VisitRecord[];
  start: () => Promise<void>;                 // 出门探索：POST /api/explore（无 topic）
  choose: (choice: string) => Promise<void>;  // 提交决策 node:0/safe_room/descend/retreat
  toggleAutopilot: (on: boolean) => Promise<void>;
  onStep: (e: ExplorationStepEvent) => void;
  onActivityEnd: (activityId: string) => void;
};

export const useExplorationStore = create<ExplorationStoreState>((set, get) => ({
  decision: null,
  activityId: null,
  autopilot: false,
  choosing: false,
  error: null,
  history: [],
  start: async () => {
    set({ error: null });
    try {
      const { activity_id } = await postExplore();
      set({
        activityId: activity_id,
        decision: null,
        history: [],
        autopilot: false,
        choosing: false,
      });
    } catch (err) {
      set({ error: err instanceof Error ? err.message : String(err) });
    }
  },
  choose: async (choice) => {
    const { activityId, decision, choosing } = get();
    if (activityId === null || decision === null || choosing) return;
    const record = nodeFromChoice(choice, decision);
    const history = record === null ? get().history : [...get().history, record];
    set({ choosing: true, error: null, history });
    try {
      await chooseExploration(activityId, choice);
      // SSE 主通道：新决策经 exploration_step 的 onStep 推回并解锁 choosing
    } catch (err) {
      set({ choosing: false, error: err instanceof Error ? err.message : String(err) });
    }
  },
  toggleAutopilot: async (on) => {
    const { activityId } = get();
    if (activityId === null) return;
    set({ autopilot: on, error: null });
    try {
      await setExplorationAutopilot(activityId, on);
    } catch (err) {
      set({ autopilot: !on, error: err instanceof Error ? err.message : String(err) });
    }
  },
  onStep: (e) => {
    const d = e.decision;
    if (!Array.isArray(d?.nodes)) return; // 运行时收窄：非法载荷丢弃
    set((s) => {
      const sameRun = s.activityId === e.activity_id;
      return {
        decision: d,
        activityId: e.activity_id,
        choosing: false,
        history: sameRun ? s.history : [],
        autopilot: sameRun ? s.autopilot : false,
      };
    });
  },
  onActivityEnd: (activityId) => {
    set((s) =>
      s.activityId === activityId
        ? { decision: null, activityId: null, autopilot: false, choosing: false }
        : {},
    );
  },
}));

/** 决策字符串 → 本地足迹；非节点类（descend/retreat）不记录。 */
function nodeFromChoice(
  choice: string,
  decision: ExplorationDecision,
): VisitRecord | null {
  if (choice === "safe_room") {
    return { floor: decision.floor, name: "安全房", kind: "safe_room" };
  }
  if (choice.startsWith("node:")) {
    const idx = Number(choice.slice(5));
    const node = Number.isInteger(idx) ? decision.nodes[idx] : undefined;
    if (node !== undefined) {
      return { floor: decision.floor, name: node.name, kind: node.kind };
    }
  }
  return null;
}
