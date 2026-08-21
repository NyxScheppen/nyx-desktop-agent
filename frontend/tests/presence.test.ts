import { fireEvent, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { postObserve } from "../src/api/client";
import { classifyPresence, usePresence } from "../src/hooks/usePresence";

// usePresence 直接消费 postObserve，mock 掉以隔离 hook 的判定/上报节奏（fetch 细节归 api.test.ts）
vi.mock("../src/api/client", () => ({
  postObserve: vi.fn(),
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
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("首次挂载必报一次（初始 away）", () => {
    renderHook(() => usePresence());
    expect(postObserve).toHaveBeenCalledTimes(1);
    expect(postObserve).toHaveBeenCalledWith("away", ""); // jsdom document.title 默认 ""
  });

  it("键盘活动 → 下次采样报 online", () => {
    renderHook(() => usePresence());
    vi.advanceTimersByTime(10_000);
    fireEvent.keyDown(window);
    vi.advanceTimersByTime(20_000); // 距挂载 30s 采样；活动仅 20s 前，< 30s 窗口 → online
    expect(postObserve).toHaveBeenCalledWith("online", "");
  });

  it("鼠标活动 → 下次采样报 online", () => {
    renderHook(() => usePresence());
    vi.advanceTimersByTime(10_000);
    fireEvent.mouseMove(window);
    vi.advanceTimersByTime(20_000);
    expect(postObserve).toHaveBeenCalledWith("online", "");
  });

  it("presence 不变 → 30s 后不上报", () => {
    renderHook(() => usePresence());
    vi.advanceTimersByTime(30_000);
    expect(postObserve).toHaveBeenCalledTimes(1); // 仅挂载那次 away
  });
});
