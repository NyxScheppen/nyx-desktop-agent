import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import CreationPanel from "../src/components/panels/CreationPanel";
import { useActivityStore } from "../src/stores/activityStore";

describe("CreationPanel", () => {
  beforeEach(() => {
    vi.spyOn(useActivityStore.getState(), "refresh").mockResolvedValue(undefined);
    vi.spyOn(useActivityStore.getState(), "loadMoreResults").mockResolvedValue(
      undefined,
    );
    useActivityStore.setState({
      results: [
        {
          id: "creation-1",
          type: "creation",
          schedule_block_id: "15:00",
          status: "completed",
          progress: {
            result: {
              title: "窗边的夜色",
              content: "完整正文",
              path: "workspace/creations/night.md",
            },
          },
          started_at: 1,
          ended_at: 2,
        },
      ],
      error: null,
      hasMoreResults: true,
      resultsLoading: false,
    });
  });

  afterEach(() => vi.restoreAllMocks());

  it("书架点击作品查看正文和活动来源，可以返回并加载更多", () => {
    render(<CreationPanel />);

    expect(screen.queryByText("完整正文")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /窗边的夜色/ }));
    expect(screen.getByText("完整正文")).toBeVisible();
    expect(screen.getByText("来自创作活动 · creation-1")).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "← 返回书架" }));
    expect(screen.queryByText("完整正文")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "加载更多" }));
    expect(useActivityStore.getState().loadMoreResults).toHaveBeenCalledTimes(1);
  });
});
