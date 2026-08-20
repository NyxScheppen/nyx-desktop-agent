import { create } from "zustand";
import { getMaterials, uploadFile } from "../api/client";

// 资料面板：已上传文件清单 + 上传动作。files=null 表示「尚未加载」，[] 表示「加载过但为空」。
type MaterialsStoreState = {
  files: string[] | null;
  uploading: boolean;
  error: string | null;
  refresh: () => Promise<void>;
  upload: (file: File) => Promise<void>;
};

export const useMaterialsStore = create<MaterialsStoreState>((set) => ({
  files: null,
  uploading: false,
  error: null,
  refresh: async () => {
    set({ error: null });
    try {
      const { files } = await getMaterials();
      set({ files });
    } catch (err) {
      set({ error: err instanceof Error ? err.message : String(err) });
    }
  },
  upload: async (file) => {
    set({ uploading: true, error: null });
    try {
      await uploadFile(file);
      const { files } = await getMaterials();
      set({ files, uploading: false });
    } catch (err) {
      set({
        error: err instanceof Error ? err.message : String(err),
        uploading: false,
      });
    }
  },
}));
