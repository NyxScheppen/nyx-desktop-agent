import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import ActivityPanel from "../src/components/panels/ActivityPanel";
import { useActivityStore } from "../src/stores/activityStore";
import { useReaderStore } from "../src/stores/readerStore";
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
      tasks: [],
      error: null,
      taskError: null,
      creatingTask: false,
    });
    vi.spyOn(useReaderStore.getState(), "loadBooks").mockResolvedValue(undefined);
    useReaderStore.setState({ books: [], booksError: null });
  });

  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it("产出区只显示创作标题摘要，不显示正文或其他活动产出", () => {
    render(<ActivityPanel />);

    expect(screen.getByText("夜色")).toBeInTheDocument();
    expect(screen.queryByText("创作正文")).not.toBeInTheDocument();
    expect(screen.queryByText("读书标题")).not.toBeInTheDocument();
    expect(screen.queryByText("读书笔记")).not.toBeInTheDocument();
  });

  it("书籍任务显示目标前后文并提交已选段落", async () => {
    const create = vi
      .spyOn(useActivityStore.getState(), "createBookTask")
      .mockResolvedValue(true);
    useReaderStore.setState({
      books: [
        {
          id: "b1",
          title: "诺斯艾兰",
          author: "安妮",
          filename: "book.epub",
          total_paragraphs: 20,
          user_position: 5,
          last_read_at: 1,
        },
      ],
    });
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        status: 200,
        json: async () => [
          { id: "p4", book_id: "b1", index: 4, text: "前一段", is_chapter_start: false },
          { id: "p5", book_id: "b1", index: 5, text: "目标段", is_chapter_start: false },
          { id: "p6", book_id: "b1", index: 6, text: "后一段", is_chapter_start: false },
        ],
      } as Response),
    );

    render(<ActivityPanel />);
    fireEvent.click(screen.getByRole("button", { name: "书籍" }));

    await waitFor(() => expect(screen.getByText("目标段")).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "安排阅读" }));

    expect(create).toHaveBeenCalledWith("b1", 5);
    expect(screen.getByText("前一段")).toBeInTheDocument();
    expect(screen.getByText("后一段")).toBeInTheDocument();
  });

  it("任务列表显示状态与失败原因", () => {
    useActivityStore.setState({
      tasks: [
        {
          id: "t1",
          type: "web",
          status: "failed",
          url: "https://example.com/article",
          book_id: null,
          target_paragraph: null,
          checkpoint: {},
          error: "正文抓取失败",
          created_at: 1,
          updated_at: 2,
        },
      ],
    });

    render(<ActivityPanel />);

    expect(screen.getByText("失败")).toBeInTheDocument();
    expect(screen.getByText("正文抓取失败")).toBeInTheDocument();
  });
});
