import { create } from "zustand";
import { getMaterials, uploadFile } from "../api/client";
import type { Material } from "../types/api";

// 资料面板：已上传读物清单（含进度）+ 上传动作。materials=null 表示「尚未加载」，
// [] 表示「加载过但为空」。
type MaterialsStoreState = {
  materials: Material[] | null;
  uploading: boolean;
  error: string | null;
  refresh: () => Promise<void>;
  upload: (file: File) => Promise<void>;
};

export const useMaterialsStore = create<MaterialsStoreState>((set) => ({
  materials: null,
  uploading: false,
  error: null,
  refresh: async () => {
    set({ error: null });
    try {
      const { materials } = await getMaterials();
      set({ materials });
    } catch (err) {
      set({ error: err instanceof Error ? err.message : String(err) });
    }
  },
  upload: async (file) => {
    set({ uploading: true, error: null });
    try {
      await uploadFile(file);
      const { materials } = await getMaterials();
      set({ materials, uploading: false });
    } catch (err) {
      set({
        error: err instanceof Error ? err.message : String(err),
        uploading: false,
      });
    }
  },
}));
