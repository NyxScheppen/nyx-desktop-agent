import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { invoke } from "@tauri-apps/api/core";
import { postObserve } from "../src/api/client";
import { classifyPresence, usePresence } from "../src/hooks/usePresence";

// usePresence 直接消费 postObserve，mock 掉以隔离 hook 的判定/上报节奏（fetch 细节归 api.test.ts）
vi.mock("../src/api/client", () => ({
  postObserve: vi.fn(),
}));
vi.mock("@tauri-apps/api/core", () => ({
  invoke: vi.fn(),
}));

describe("classifyPresence", () => {
  it("键盘/鼠标任一活跃 → online（优先于窗口标题）", () => {
    expect(classifyPresence(true, false, "")).toBe("online");
    expect(classifyPresence(false, true, "")).toBe("online");
    expect(classifyPresence(true, true, "")).toBe("online");
    expect(classifyPresence(true, true, "编辑器")).toBe("online"); // 活跃优先于标题
  });

  it("无输入但有窗口标题 → busy；全无 → away", () => {
    expect(classifyPresence(false, false, "编辑器")).toBe("busy");
    expect(classifyPresence(false, false, "")).toBe("away");
  });
});

describe("usePresence", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-08-19T12:00:00Z"));
    vi.mocked(postObserve).mockReset();
    vi.mocked(postObserve).mockResolvedValue({ event_id: "e1" });
    vi.mocked(invoke).mockReset();
    vi.mocked(invoke).mockResolvedValue([false, ""]);
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("首次挂载通过 Tauri 采样并上报 away", async () => {
    renderHook(() => usePresence());
    await act(async () => Promise.resolve());
    expect(invoke).toHaveBeenCalledWith("sample_presence", {
      activeWindowMs: 30_000,
    });
    expect(postObserve).toHaveBeenCalledTimes(1);
    expect(postObserve).toHaveBeenCalledWith("away", "");
  });

  it("原生输入活跃时上报 online", async () => {
    vi.mocked(invoke).mockResolvedValue([true, "编辑器"]);
    renderHook(() => usePresence());
    await act(async () => Promise.resolve());
    expect(postObserve).toHaveBeenCalledWith("online", "编辑器");
  });

  it("presence 和前台标题不变时不重复上报", async () => {
    renderHook(() => usePresence());
    await act(async () => Promise.resolve());
    await act(async () => {
      vi.advanceTimersByTime(30_000);
      await Promise.resolve();
    });
    expect(postObserve).toHaveBeenCalledTimes(1); // 仅挂载那次 away
  });
});
