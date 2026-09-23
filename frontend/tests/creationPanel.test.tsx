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

  it("创作正文默认折叠，并提供加载更多", () => {
    render(<CreationPanel />);

    const summary = screen.getByText("窗边的夜色");
    const details = summary.closest("details");
    const body = screen.getByText("完整正文");
    expect(details).not.toHaveAttribute("open");
    expect(body).not.toBeVisible();

    fireEvent.click(summary);
    expect(details).toHaveAttribute("open");
    expect(body).toBeVisible();

    fireEvent.click(screen.getByRole("button", { name: "加载更多" }));
    expect(useActivityStore.getState().loadMoreResults).toHaveBeenCalledTimes(1);
  });
});
