export type DesktopMode = "pet" | "full";

type TauriWindow = {
  setSize: (size: unknown) => Promise<void>;
  setResizable: (resizable: boolean) => Promise<void>;
  setAlwaysOnTop: (alwaysOnTop: boolean) => Promise<void>;
  setAlwaysOnBottom: (alwaysOnBottom: boolean) => Promise<void>;
  center: () => Promise<void>;
  startDragging: () => Promise<void>;
};

type TauriWindowModule = {
  getCurrentWindow: () => TauriWindow;
  LogicalSize: new (width: number, height: number) => unknown;
};

export function isTauriRuntime(): boolean {
  return typeof window !== "undefined" &&
    (window as Window & { __TAURI_INTERNALS__?: unknown }).__TAURI_INTERNALS__ !== undefined;
}

async function getWindowModule(): Promise<TauriWindowModule | null> {
  if (!isTauriRuntime()) return null;
  return import("@tauri-apps/api/window") as Promise<TauriWindowModule>;
}

/** 调整桌宠/完整桌面端窗口；浏览器开发与测试环境保持普通 Web 页面。 */
export async function applyDesktopMode(mode: DesktopMode): Promise<void> {
  const module = await getWindowModule();
  if (module === null) return;
  const appWindow = module.getCurrentWindow();
  const size = mode === "pet" ? [560, 520] : [1200, 820];
  await appWindow.setSize(new module.LogicalSize(size[0], size[1]));
  await appWindow.setResizable(mode === "full");
  await appWindow.setAlwaysOnTop(false);
  // 桌宠不置顶，也不强制置底：always-on-bottom 会把窗口压到其他应用后面，
  // 用户因此既看不见它，也无法点击头像触发原生拖动。
  await appWindow.setAlwaysOnBottom(false);
  if (mode === "full") await appWindow.center();
}

/** Tauri 中把头像拖拽交给原生窗口；浏览器里由 Avatar 的 CSS 回退拖动。 */
export async function startNativeWindowDrag(): Promise<void> {
  const module = await getWindowModule();
  if (module !== null) await module.getCurrentWindow().startDragging();
}
