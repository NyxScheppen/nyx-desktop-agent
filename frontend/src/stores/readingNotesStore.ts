import { create } from "zustand";
import { deleteReadingNote, getReadingNotes } from "../api/client";
import type { ReadingNote } from "../types/api";

// 读书笔记面板：笔记清单 + 删除动作（批注增删查用组件本地 state，不入 store）。
// notes=null 表示「尚未加载」，[] 表示「加载过但为空」。
type ReadingNotesStoreState = {
  notes: ReadingNote[] | null;
  loading: boolean;
  error: string | null;
  refresh: () => Promise<void>;
  remove: (noteId: string) => Promise<void>;
};

export const useReadingNotesStore = create<ReadingNotesStoreState>((set) => ({
  notes: null,
  loading: false,
  error: null,
  refresh: async () => {
    set({ loading: true, error: null });
    try {
      const notes = await getReadingNotes(50);
      set({ notes, loading: false });
    } catch (err) {
      set({ error: err instanceof Error ? err.message : String(err), loading: false });
    }
  },
  remove: async (noteId) => {
    set({ error: null });
    try {
      await deleteReadingNote(noteId);
      set((s) => ({
        notes: (s.notes ?? []).filter((n) => n.id !== noteId),
      }));
    } catch (err) {
      set({ error: err instanceof Error ? err.message : String(err) });
    }
  },
}));
