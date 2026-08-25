import { render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import NarrativePanel from "../src/components/panels/NarrativePanel";
import { useNarrativeStore } from "../src/stores/narrativeStore";

// 叙事面板高亮：reflection_done 命中的新故事条目标「新」徽标（12-inner-life §反思完成）。
describe("NarrativePanel 高亮", () => {
  beforeEach(() => {
    // 阻断 mount 时的 refresh() 真实 fetch（本测只验高亮渲染，不验数据拉取）
    vi.spyOn(useNarrativeStore.getState(), "refresh").mockResolvedValue(undefined);
    useNarrativeStore.setState({
      data: {
        identity: "尼克斯",
        story: ["初始故事", "今天对用户了解更多"],
        self_view: { 自信: "稍强" },
        becoming: ["我更愿意探索了"],
        updated_at: 1000,
      },
      error: null,
      highlightedStory: null,
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("highlightedStory 命中 → 该故事条目标「新」徽标", () => {
    useNarrativeStore.setState({ highlightedStory: "今天对用户了解更多" });
    render(<NarrativePanel />);
    const badge = screen.getByText("新");
    expect(badge.closest("li")?.textContent).toContain("今天对用户了解更多");
  });

  it("highlightedStory 未命中 → 不渲染徽标", () => {
    render(<NarrativePanel />);
    expect(screen.queryByText("新")).not.toBeInTheDocument();
  });
});
