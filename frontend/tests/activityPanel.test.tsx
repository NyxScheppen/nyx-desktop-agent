import { render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import ActivityPanel from "../src/components/panels/ActivityPanel";
import { useActivityStore } from "../src/stores/activityStore";
import type { Activity } from "../src/types/api";

function result(
  id: string,
  type: Activity["type"],
  title: string,
  content: string,
): Activity {
  return {
    id,
    type,
    schedule_block_id: "15:00",
    status: "completed",
    progress: {
      result:
        type === "creation"
          ? { title, content }
          : { book: title, note: content },
    },
    started_at: 1,
    ended_at: 2,
  };
}

describe("ActivityPanel", () => {
  beforeEach(() => {
    vi.spyOn(useActivityStore.getState(), "refresh").mockResolvedValue(undefined);
    useActivityStore.setState({
      data: { current: null, schedule: [] },
      results: [
        result("creation", "creation", "夜色", "创作正文"),
        result("reading", "reading", "读书标题", "读书笔记"),
      ],
      error: null,
    });
  });

  afterEach(() => vi.restoreAllMocks());

  it("产出区只显示创作标题摘要，不显示正文或其他活动产出", () => {
    render(<ActivityPanel />);

    expect(screen.getByText("夜色")).toBeInTheDocument();
    expect(screen.queryByText("创作正文")).not.toBeInTheDocument();
    expect(screen.queryByText("读书标题")).not.toBeInTheDocument();
    expect(screen.queryByText("读书笔记")).not.toBeInTheDocument();
  });
});
