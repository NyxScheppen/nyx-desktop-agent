import { create } from "zustand";

// 外观设置（背景色调 + 背景图 + 字体大小）：纯前端 UI 状态，无后端契约。
// tint = 背景主色（hex，null = 默认羊皮纸）；image = 上传背景图 data URL（null = 无图）。
// 二者独立可共存：有图时以图铺底、色调叠一层半透明滤镜；无图时色调直接作为背景色。
// fontScale = 字体大小档位（"small" | "medium" | "large"，默认 "medium"），驱动 App 注入 --text-scale。
type FontScale = "small" | "medium" | "large";

type SettingsState = {
  tint: string | null;
  image: string | null;
  fontScale: FontScale;
  setTint: (tint: string | null) => void;
  setImage: (image: string | null) => void;
  setFontScale: (fontScale: FontScale) => void;
  reset: () => void;
};

export const useSettingsStore = create<SettingsState>((set) => ({
  tint: null,
  image: null,
  fontScale: "medium",
  setTint: (tint) => set({ tint }),
  setImage: (image) => set({ image }),
  setFontScale: (fontScale) => set({ fontScale }),
  reset: () => set({ tint: null, image: null, fontScale: "medium" }),
}));
