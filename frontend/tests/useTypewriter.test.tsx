import { act, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useTypewriter } from "../src/hooks/useTypewriter";

describe("useTypewriter", () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  it("空文本 → displayed 空 + done 立即 true", () => {
    const { result } = renderHook(() => useTypewriter(""));
    expect(result.current.displayed).toBe("");
    expect(result.current.done).toBe(true);
  });

  it("逐字：每 tick 增一字，直至 done", () => {
    vi.useFakeTimers();
    const { result } = renderHook(() => useTypewriter("你好", 35));

    expect(result.current.displayed).toBe("");
    expect(result.current.done).toBe(false);

    act(() => vi.advanceTimersByTime(35));
    expect(result.current.displayed).toBe("你");
    expect(result.current.done).toBe(false);

    act(() => vi.advanceTimersByTime(35));
    expect(result.current.displayed).toBe("你好");
    expect(result.current.done).toBe(true);
  });

  it("ready=false：不启动逐字（displayed 空 + done false），转 true 才开打", () => {
    vi.useFakeTimers();
    const { result, rerender } = renderHook(
      ({ ready }) => useTypewriter("你好", 35, ready),
      { initialProps: { ready: false } },
    );

    expect(result.current.displayed).toBe("");
    expect(result.current.done).toBe(false);

    act(() => vi.advanceTimersByTime(100)); // 未就绪，推进 timer 也不打字
    expect(result.current.displayed).toBe("");

    rerender({ ready: true });
    act(() => vi.advanceTimersByTime(35));
    expect(result.current.displayed).toBe("你");
    expect(result.current.done).toBe(false);
  });
});
