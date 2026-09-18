import { act, fireEvent, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { invoke } from "@tauri-apps/api/core";
import { postObserve } from "../src/api/client";
import { classifyPresence, usePresence } from "../src/hooks/usePresence";
import type { ConnectionState } from "../src/types/api";

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
    vi.useFakeTimers({
      toFake: ["setTimeout", "clearTimeout", "setInterval", "clearInterval", "Date", "performance"],
    });
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
    renderHook(() => usePresence("open"));
    await act(async () => Promise.resolve());
    expect(invoke).toHaveBeenCalledWith("sample_presence");
    expect(postObserve).toHaveBeenCalledTimes(1);
    expect(postObserve).toHaveBeenCalledWith(expect.objectContaining({ presence: "away", window_title: "", idle_seconds: 300, sampled_at: Date.now() / 1000 }), expect.any(AbortSignal));
  });

  it("原生 idle 很短时上报 online", async () => {
    vi.mocked(invoke).mockResolvedValue([1_000, "编辑器"]);
    renderHook(() => usePresence("open"));
    await act(async () => Promise.resolve());
    expect(postObserve).toHaveBeenCalledWith(expect.objectContaining({ presence: "online", window_title: "编辑器", idle_seconds: 1 }), expect.any(AbortSignal));
  });

  it("窗口标题非空不阻止 away", async () => {
    vi.mocked(invoke).mockResolvedValue([300_000, "编辑器"]);
    renderHook(() => usePresence("open"));
    await act(async () => Promise.resolve());
    expect(postObserve).toHaveBeenCalledWith(expect.objectContaining({ presence: "away", window_title: "编辑器", idle_seconds: 300 }), expect.any(AbortSignal));
  });

  it("Tauri 失败时按 WebView 输入时间降级，归来后恢复 online", async () => {
    vi.mocked(invoke).mockRejectedValue(new Error("browser"));
    renderHook(() => usePresence("open"));
    await act(async () => Promise.resolve());
    expect(postObserve).toHaveBeenLastCalledWith(expect.objectContaining({ presence: "online", window_title: "", idle_seconds: 0 }), expect.any(AbortSignal));

    await act(async () => vi.advanceTimersByTimeAsync(300_000));
    expect(postObserve).toHaveBeenCalledWith(expect.objectContaining({ presence: "busy", idle_seconds: 30 }), expect.any(AbortSignal));
    expect(postObserve).toHaveBeenLastCalledWith(expect.objectContaining({ presence: "away", idle_seconds: 300 }), expect.any(AbortSignal));

    await act(async () => vi.advanceTimersByTimeAsync(29_999));
    fireEvent.keyDown(window, { key: "a" });
    await act(async () => vi.advanceTimersByTimeAsync(1));
    expect(postObserve).toHaveBeenLastCalledWith(expect.objectContaining({ presence: "online", idle_seconds: 0.001 }), expect.any(AbortSignal));
  });

  it("presence 和前台标题不变时不重复上报", async () => {
    renderHook(() => usePresence("open"));
    await act(async () => Promise.resolve());
    await act(async () => {
      vi.advanceTimersByTime(30_000);
      await Promise.resolve();
    });
    expect(postObserve).toHaveBeenCalledTimes(1); // 仅挂载那次 away
  });

  it("系统时钟回拨、presence 不变时也上报新基线", async () => {
    renderHook(() => usePresence("open"));
    await act(async () => Promise.resolve());
    vi.setSystemTime(Date.now() - 3_600_000);
    await act(async () => { fireEvent.focus(window); });
    expect(postObserve).toHaveBeenCalledTimes(2);
  });

  it.each([-3_600_000, 86_400_000])("时钟跳变 %s 不影响 WebView 闲置时长", async (jump) => {
    vi.mocked(invoke).mockRejectedValue(new Error("browser"));
    renderHook(() => usePresence("open"));
    await act(async () => Promise.resolve());
    vi.setSystemTime(Date.now() + jump);
    await act(async () => vi.advanceTimersByTimeAsync(300_000));
    expect(postObserve).toHaveBeenLastCalledWith(expect.objectContaining({
      presence: "away", idle_seconds: 300,
    }), expect.any(AbortSignal));
  });

  it("POST 失败不推进 last-sent，下次采样重试", async () => {
    vi.mocked(postObserve)
      .mockRejectedValueOnce(new Error("offline"))
      .mockResolvedValueOnce({ event_id: "e2" });
    renderHook(() => usePresence("open"));
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
    renderHook(() => usePresence("open"));
    await act(async () => Promise.resolve());
    await act(async () => { fireEvent.focus(window); });
    await act(async () => { fireEvent.focus(window); });
    expect(postObserve).toHaveBeenCalledTimes(1);

    await act(async () => {
      finishFirst({ event_id: "first" });
      await Promise.resolve();
    });

    expect(postObserve).toHaveBeenCalledTimes(2);
    expect(postObserve).toHaveBeenLastCalledWith(expect.objectContaining({ presence: "away", window_title: "C", idle_seconds: 300 }), expect.any(AbortSignal));
  });

  it("A 已成功、B 在途、最新采样回到 A 时不会丢掉 A", async () => {
    let finishB!: (value: { event_id: string }) => void;
    vi.mocked(postObserve)
      .mockResolvedValueOnce({ event_id: "A" })
      .mockReturnValueOnce(new Promise((resolve) => { finishB = resolve; }))
      .mockResolvedValue({ event_id: "A-again" });
    vi.mocked(invoke)
      .mockResolvedValueOnce([0, "Editor"])
      .mockResolvedValueOnce([300_000, "Editor"])
      .mockResolvedValue([0, "Editor"]);
    renderHook(() => usePresence("open"));
    await act(async () => Promise.resolve());
    await act(async () => { fireEvent.focus(window); });
    await act(async () => { fireEvent.focus(window); });
    await act(async () => { finishB({ event_id: "B" }); });
    expect(postObserve).toHaveBeenCalledTimes(3);
  });

  it("SSE 重连后，即使 presence 和窗口没变也立即重新上报", async () => {
    vi.mocked(invoke).mockResolvedValue([0, "Editor"]);
    const { rerender } = renderHook(
      ({ status }: { status: ConnectionState }) => usePresence(status),
      { initialProps: { status: "open" as ConnectionState } },
    );
    await act(async () => Promise.resolve());
    const before = vi.mocked(postObserve).mock.calls.length;
    rerender({ status: "connecting" });
    await act(async () => Promise.resolve());
    rerender({ status: "open" });
    await act(async () => Promise.resolve());
    expect(vi.mocked(postObserve).mock.calls.length).toBeGreaterThan(before);
  });

  it("原生采样乱序完成时丢弃旧结果", async () => {
    let finishOld!: (value: [number, string]) => void;
    vi.mocked(invoke)
      .mockReturnValueOnce(new Promise((resolve) => { finishOld = resolve; }))
      .mockResolvedValue([0, "Current"]);
    renderHook(() => usePresence("open"));
    await act(async () => vi.advanceTimersByTimeAsync(30_000));
    await act(async () => { finishOld([300_000, "Old"]); });
    expect(postObserve).toHaveBeenCalledTimes(1);
  });

  it.each([NaN, Infinity, -1, "0", true, null])("非法原生 idle %s 走 WebView fallback", async (idle) => {
    vi.mocked(invoke).mockResolvedValue([idle, "Native"]);
    renderHook(() => usePresence("open"));
    await act(async () => vi.advanceTimersByTimeAsync(300_000));
    expect(postObserve).toHaveBeenLastCalledWith(expect.objectContaining({
      presence: "away", idle_seconds: 300, window_title: "",
    }), expect.any(AbortSignal));
  });

  it.each([null, {}, [0], [0, {}], [0, "", "extra"]])("非法原生 tuple %s 降级而不崩", async (tuple) => {
    vi.mocked(invoke).mockResolvedValue(tuple);
    renderHook(() => usePresence("open"));
    await act(async () => Promise.resolve());
    expect(postObserve).toHaveBeenCalledWith(expect.objectContaining({
      presence: "online", idle_seconds: 0, window_title: "",
    }), expect.any(AbortSignal));
  });

  it("超长原生标题截断到协议上限", async () => {
    vi.mocked(invoke).mockResolvedValue([0, "X".repeat(10_000)]);
    renderHook(() => usePresence("open"));
    await act(async () => Promise.resolve());
    expect(vi.mocked(postObserve).mock.calls[0][0].window_title).toHaveLength(512);
  });

  it("标题截断不切开 UTF-16 代理对", async () => {
    const title = "X".repeat(511) + "\uD83D\uDE00";
    vi.mocked(invoke).mockResolvedValue([0, title + "tail"]);
    renderHook(() => usePresence("open"));
    await act(async () => Promise.resolve());
    expect(vi.mocked(postObserve).mock.calls[0][0].window_title).toBe(title);
  });

  it("请求挂起 10 秒取消并允许后续采样重试", async () => {
    vi.mocked(postObserve)
      .mockReturnValueOnce(new Promise(() => undefined))
      .mockResolvedValue({ event_id: "retried" });
    renderHook(() => usePresence("open"));
    await act(async () => Promise.resolve());
    const signal = vi.mocked(postObserve).mock.calls[0][1];
    await act(async () => vi.advanceTimersByTimeAsync(10_000));
    expect(signal?.aborted).toBe(true);
    await act(async () => vi.advanceTimersByTimeAsync(20_000));
    expect(postObserve).toHaveBeenCalledTimes(2);
  });

  it("卸载取消在途请求，迟到采样不能继续上报", async () => {
    let finishSample!: (value: [number, string]) => void;
    vi.mocked(invoke)
      .mockResolvedValueOnce([0, "A"])
      .mockReturnValueOnce(new Promise((resolve) => { finishSample = resolve; }));
    vi.mocked(postObserve).mockReturnValue(new Promise(() => undefined));
    const { unmount } = renderHook(() => usePresence("open"));
    await act(async () => Promise.resolve());
    await act(async () => { fireEvent.focus(window); });
    const signal = vi.mocked(postObserve).mock.calls[0][1];
    unmount();
    await act(async () => { finishSample([300_000, "late"]); });
    expect(signal?.aborted).toBe(true);
    expect(postObserve).toHaveBeenCalledTimes(1);
  });

  it("B 网络失败后不能假定后端仍是上一次成功的 A", async () => {
    let failB!: (reason: Error) => void;
    vi.mocked(postObserve)
      .mockResolvedValueOnce({ event_id: "A" })
      .mockReturnValueOnce(new Promise((_, reject) => { failB = reject; }))
      .mockResolvedValue({ event_id: "A-confirmed" });
    vi.mocked(invoke)
      .mockResolvedValueOnce([0, "A"])
      .mockResolvedValueOnce([300_000, "B"])
      .mockResolvedValue([0, "A"]);
    renderHook(() => usePresence("open"));
    await act(async () => Promise.resolve());
    await act(async () => { fireEvent.focus(window); });
    await act(async () => { fireEvent.focus(window); });
    await act(async () => { failB(new Error("response lost after server committed")); });
    await act(async () => vi.advanceTimersByTimeAsync(30_000));
    expect(postObserve).toHaveBeenCalledTimes(3);
    expect(vi.mocked(postObserve).mock.calls[2][0].window_title).toBe("A");
  });
});
