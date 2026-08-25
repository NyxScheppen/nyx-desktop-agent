import { create } from "zustand";
import { postExplore } from "../api/client";
import type { ExplorationNode, ExplorationStepEvent } from "../types/api";

// 探索地图（design §4.1）：实时节点 + 心愿单。心愿单 MVP 只存前端内存，不落库。
type ExplorationStoreState = {
  wishlist: string[];                 // 待探索心愿（主题词）
  liveNodes: ExplorationNode[];       // 当前探索实时累积的节点（exploration_step 推送）
  activityId: string | null;          // 当前探索 activity_id
  addWish: (topic: string) => void;
  removeWish: (topic: string) => void;
  start: () => Promise<void>;         // 出门探索：POST /api/explore（无 topic）
  onStep: (e: ExplorationStepEvent) => void;
};

export const useExplorationStore = create<ExplorationStoreState>((set) => ({
  wishlist: [],
  liveNodes: [],
  activityId: null,
  addWish: (topic) => {
    const t = topic.trim();
    if (t === "") return;
    set((s) =>
      s.wishlist.includes(t) ? {} : { wishlist: [...s.wishlist, t] },
    );
  },
  removeWish: (topic) =>
    set((s) => ({ wishlist: s.wishlist.filter((w) => w !== topic) })),
  start: async () => {
    const { activity_id } = await postExplore();
    set({ activityId: activity_id, liveNodes: [] });
  },
  onStep: (e) => {
    const node = e.node;
    if (typeof node?.name !== "string") return; // 运行时收窄
    set((s) =>
      s.activityId === e.activity_id
        ? { liveNodes: [...s.liveNodes, node] }
        : { activityId: e.activity_id, liveNodes: [node] },
    );
  },
}));
