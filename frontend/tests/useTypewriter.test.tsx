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
});
