import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { applyDesktopMode, isTauriRuntime, startNativeWindowDrag } from "../src/lib/desktopWindow";

const windowMocks = vi.hoisted(() => ({
  setSize: vi.fn(async () => undefined),
  setResizable: vi.fn(async () => undefined),
  setAlwaysOnTop: vi.fn(async () => undefined),
  setAlwaysOnBottom: vi.fn(async () => undefined),
  center: vi.fn(async () => undefined),
  startDragging: vi.fn(async () => undefined),
}));

vi.mock("@tauri-apps/api/window", () => ({
  getCurrentWindow: () => windowMocks,
  LogicalSize: class LogicalSize {
    constructor(public readonly width: number, public readonly height: number) {}
  },
}));

function enableTauriRuntime(): void {
  (window as Window & { __TAURI_INTERNALS__?: unknown }).__TAURI_INTERNALS__ = {};
}

function disableTauriRuntime(): void {
  delete (window as Window & { __TAURI_INTERNALS__?: unknown }).__TAURI_INTERNALS__;
}

beforeEach(() => {
  vi.clearAllMocks();
  enableTauriRuntime();
});

afterEach(() => {
  disableTauriRuntime();
});

describe("desktopWindow", () => {
  it("浏览器环境安全降级，不调用 Tauri 窗口 API", async () => {
    disableTauriRuntime();

    expect(isTauriRuntime()).toBe(false);
    await applyDesktopMode("pet");
    await startNativeWindowDrag();

    expect(windowMocks.setSize).not.toHaveBeenCalled();
    expect(windowMocks.startDragging).not.toHaveBeenCalled();
  });

  it("桌宠态移动整个窗口并保持普通窗口层级", async () => {
    await applyDesktopMode("pet");

    expect(windowMocks.setSize).toHaveBeenCalledWith(expect.objectContaining({ width: 560, height: 520 }));
    expect(windowMocks.setResizable).toHaveBeenCalledWith(false);
    expect(windowMocks.setAlwaysOnTop).toHaveBeenCalledWith(false);
    expect(windowMocks.setAlwaysOnBottom).toHaveBeenCalledWith(false);

    await startNativeWindowDrag();
    expect(windowMocks.startDragging).toHaveBeenCalledTimes(1);
  });

  it("完整桌面端恢复普通层级并居中", async () => {
    await applyDesktopMode("full");

    expect(windowMocks.setSize).toHaveBeenCalledWith(expect.objectContaining({ width: 1200, height: 820 }));
    expect(windowMocks.setResizable).toHaveBeenCalledWith(true);
    expect(windowMocks.setAlwaysOnTop).toHaveBeenCalledWith(false);
    expect(windowMocks.setAlwaysOnBottom).toHaveBeenCalledWith(false);
    expect(windowMocks.center).toHaveBeenCalledTimes(1);
  });
});
