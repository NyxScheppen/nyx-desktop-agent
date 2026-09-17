import { act, fireEvent, renderHook } from "@testing-library/react";
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
  it("按 30 秒/5 分钟边界分类，窗口标题不参与", () => {
    expect(classifyPresence(0)).toBe("online");
    expect(classifyPresence(29_999)).toBe("online");
    expect(classifyPresence(30_000)).toBe("busy");
    expect(classifyPresence(299_999)).toBe("busy");
    expect(classifyPresence(300_000)).toBe("away");
  });
});

describe("usePresence", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-08-19T12:00:00Z"));
    vi.mocked(postObserve).mockReset();
    vi.mocked(postObserve).mockResolvedValue({ event_id: "e1" });
    vi.mocked(invoke).mockReset();
    vi.mocked(invoke).mockResolvedValue([300_000, ""]);
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("首次挂载通过 Tauri 采样并上报 away", async () => {
    renderHook(() => usePresence());
    await act(async () => Promise.resolve());
    expect(invoke).toHaveBeenCalledWith("sample_presence");
    expect(postObserve).toHaveBeenCalledTimes(1);
    expect(postObserve).toHaveBeenCalledWith("away", "", 300);
  });

  it("原生 idle 很短时上报 online", async () => {
    vi.mocked(invoke).mockResolvedValue([1_000, "编辑器"]);
    renderHook(() => usePresence());
    await act(async () => Promise.resolve());
    expect(postObserve).toHaveBeenCalledWith("online", "编辑器", 1);
  });

  it("窗口标题非空不阻止 away", async () => {
    vi.mocked(invoke).mockResolvedValue([300_000, "编辑器"]);
    renderHook(() => usePresence());
    await act(async () => Promise.resolve());
    expect(postObserve).toHaveBeenCalledWith("away", "编辑器", 300);
  });

  it("Tauri 失败时按 WebView 输入时间降级，归来后恢复 online", async () => {
    vi.mocked(invoke).mockRejectedValue(new Error("browser"));
    renderHook(() => usePresence());
    await act(async () => Promise.resolve());
    expect(postObserve).toHaveBeenLastCalledWith("online", "", 0);

    await act(async () => vi.advanceTimersByTimeAsync(300_000));
    expect(postObserve).toHaveBeenCalledWith("busy", "", 30);
    expect(postObserve).toHaveBeenLastCalledWith("away", "", 300);

    await act(async () => vi.advanceTimersByTimeAsync(29_999));
    fireEvent.keyDown(window, { key: "a" });
    await act(async () => vi.advanceTimersByTimeAsync(1));
    expect(postObserve).toHaveBeenLastCalledWith("online", "", 0.001);
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

  it("POST 失败不推进 last-sent，下次采样重试", async () => {
    vi.mocked(postObserve)
      .mockRejectedValueOnce(new Error("offline"))
      .mockResolvedValueOnce({ event_id: "e2" });
    renderHook(() => usePresence());
    await act(async () => Promise.resolve());
    await act(async () => {
      vi.advanceTimersByTime(30_000);
      await Promise.resolve();
    });
    expect(postObserve).toHaveBeenCalledTimes(2);
  });

  it("single-flight 在旧请求完成后只发送最新待上报快照", async () => {
    let finishFirst!: (value: { event_id: string }) => void;
    vi.mocked(postObserve)
      .mockReturnValueOnce(new Promise((resolve) => {
        finishFirst = resolve;
      }))
      .mockResolvedValue({ event_id: "latest" });
    vi.mocked(invoke)
      .mockResolvedValueOnce([0, "A"])
      .mockResolvedValueOnce([40_000, "B"])
      .mockResolvedValueOnce([300_000, "C"]);
    renderHook(() => usePresence());
    await act(async () => Promise.resolve());
    await act(async () => {
      vi.advanceTimersByTime(60_000);
      await Promise.resolve();
    });
    expect(postObserve).toHaveBeenCalledTimes(1);

    await act(async () => {
      finishFirst({ event_id: "first" });
      await Promise.resolve();
    });

    expect(postObserve).toHaveBeenCalledTimes(2);
    expect(postObserve).toHaveBeenLastCalledWith("away", "C", 300);
  });
});
