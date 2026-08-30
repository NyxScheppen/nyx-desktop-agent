import { create } from "zustand";

// 碎碎念（mutter）独立存储：mutter 不再是聊天消息（不进 chatStore），
// 左栏下方 MutterCard 从这里读最近几条常驻展示。只存本次会话，不落历史。
export type MutterItem = {
  id: string; // event_id
  text: string;
};

type MutterState = {
  mutters: MutterItem[];
  addMutter: (id: string, text: string) => void;
  reset: () => void;
};

export const useMutterStore = create<MutterState>((set) => ({
  mutters: [],
  addMutter: (id, text) =>
    set((s) => ({ mutters: [...s.mutters, { id, text }] })),
  reset: () => set({ mutters: [] }),
}));
