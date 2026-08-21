import { create } from "zustand";

// 头像旁临时气泡（几秒后淡出）：碎碎念（mutter）+ 活动完成后冒一句（activity）。
// 纯前端呈现层 store：SSE 驱动 announce()，AnnounceLayer 订阅 items 渲染 + CSS 淡出，
// 到时 dismiss() 摘除。不落聊天历史（聊天历史仍走 chatStore）。
export type AnnounceKind = "mutter" | "activity";

export type Announcement = {
  id: string;
  kind: AnnounceKind;
  text: string;
};

// 淡出时长（ms）：碎碎念短、活动产出长。AnnounceLayer 用它设 animation-duration，与 dismiss 同步。
export const ANNOUNCE_DURATION: Record<AnnounceKind, number> = {
  mutter: 4000,
  activity: 7000,
};

type AnnounceState = {
  items: Announcement[];
  announce: (kind: AnnounceKind, text: string) => void;
  dismiss: (id: string) => void;
};

// 自增 id（不依赖 Date.now，测试可预测）；timer 交给 dismiss 前的 setTimeout 即可，不进 state。
let seq = 0;

export const useAnnounceStore = create<AnnounceState>((set, get) => ({
  items: [],
  announce: (kind, text) => {
    const id = `announce-${++seq}`;
    set((s) => ({ items: [...s.items, { id, kind, text }] }));
    setTimeout(() => get().dismiss(id), ANNOUNCE_DURATION[kind]);
  },
  dismiss: (id) => {
    set((s) => ({ items: s.items.filter((it) => it.id !== id) }));
  },
}));
