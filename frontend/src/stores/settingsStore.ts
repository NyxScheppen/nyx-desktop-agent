import { create } from "zustand";

// 背景/外观设置：纯前端 UI 状态，无后端契约。
// tint/image/fontScale 保持内存态（MVP 不持久化）；circleColor/avatarPos 读写 localStorage
// （用户点名「记住位置和底色」，下次启动原位/原色）。localStorage 不可用时静默降级为内存态。

type FontScale = "small" | "medium" | "large";

/** 头像圆圈尺寸三档（小/中/大），像素值见 Avatar.tsx 的 CIRCLE_SIZES。 */
export type CircleSize = "small" | "medium" | "large";

/** 头像圆圈拖拽后的视口坐标（px）；null = 未拖拽，用默认右下角。 */
export type AvatarPos = { x: number; y: number };

const DEFAULT_CIRCLE_COLOR = "#ffffff";
const DEFAULT_CIRCLE_SIZE: CircleSize = "large";
const CIRCLE_COLOR_KEY = "nyx.circleColor";
const AVATAR_POS_KEY = "nyx.avatarPos";
const CIRCLE_SIZE_KEY = "nyx.circleSize";

/** 纯函数：localStorage 原始串 → AvatarPos；坏 JSON / 缺 x/y / 非有限数 → null。 */
export function parseAvatarPos(raw: string | null): AvatarPos | null {
  if (raw === null) return null;
  try {
    const parsed: unknown = JSON.parse(raw);
    if (typeof parsed !== "object" || parsed === null) return null;
    const { x, y } = parsed as AvatarPos;
    if (typeof x !== "number" || !Number.isFinite(x)) return null;
    if (typeof y !== "number" || !Number.isFinite(y)) return null;
    return { x, y };
  } catch {
    return null;
  }
}

/** 纯函数：localStorage 原始串 → CircleSize；非法值回退默认「大」。 */
export function parseCircleSize(raw: string | null): CircleSize {
  if (raw === "small" || raw === "medium" || raw === "large") return raw;
  return DEFAULT_CIRCLE_SIZE;
}

// localStorage 读写：隐私模式/禁用时 getItem/setItem 抛异常，静默降级（外观非关键路径）。
function readLocal(key: string): string | null {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}

function writeLocal(key: string, value: string): void {
  try {
    localStorage.setItem(key, value);
  } catch {
    // 忽略：降级为内存态
  }
}

function removeLocal(key: string): void {
  try {
    localStorage.removeItem(key);
  } catch {
    // 忽略
  }
}

type SettingsState = {
  tint: string | null;
  image: string | null;
  fontScale: FontScale;
  circleColor: string;
  circleSize: CircleSize;
  avatarPos: AvatarPos | null;
  setTint: (tint: string | null) => void;
  setImage: (image: string | null) => void;
  setFontScale: (fontScale: FontScale) => void;
  setCircleColor: (circleColor: string) => void;
  setCircleSize: (circleSize: CircleSize) => void;
  setAvatarPos: (avatarPos: AvatarPos) => void;
  reset: () => void;
};

export const useSettingsStore = create<SettingsState>((set) => ({
  tint: null,
  image: null,
  fontScale: "medium",
  circleColor: readLocal(CIRCLE_COLOR_KEY) ?? DEFAULT_CIRCLE_COLOR,
  circleSize: parseCircleSize(readLocal(CIRCLE_SIZE_KEY)),
  avatarPos: parseAvatarPos(readLocal(AVATAR_POS_KEY)),
  setTint: (tint) => set({ tint }),
  setImage: (image) => set({ image }),
  setFontScale: (fontScale) => set({ fontScale }),
  setCircleColor: (circleColor) => {
    writeLocal(CIRCLE_COLOR_KEY, circleColor);
    set({ circleColor });
  },
  setCircleSize: (circleSize) => {
    writeLocal(CIRCLE_SIZE_KEY, circleSize);
    set({ circleSize });
  },
  setAvatarPos: (avatarPos) => {
    writeLocal(AVATAR_POS_KEY, JSON.stringify(avatarPos));
    set({ avatarPos });
  },
  reset: () => {
    removeLocal(CIRCLE_COLOR_KEY);
    removeLocal(CIRCLE_SIZE_KEY);
    removeLocal(AVATAR_POS_KEY);
    set({
      tint: null,
      image: null,
      fontScale: "medium",
      circleColor: DEFAULT_CIRCLE_COLOR,
      circleSize: DEFAULT_CIRCLE_SIZE,
      avatarPos: null,
    });
  },
}));
