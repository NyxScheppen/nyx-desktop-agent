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

  it("活动默认显示当天时间线，任务和作品有独立页面", () => {
    render(<ActivityPanel />);

    expect(screen.getByText("今天还没有活动记录")).toBeInTheDocument();
    expect(screen.queryByText("安排任务")).not.toBeInTheDocument();
    expect(screen.queryByText("夜色")).not.toBeInTheDocument();
    expect(screen.queryByText("创作正文")).not.toBeInTheDocument();
    expect(screen.queryByText("读书标题")).not.toBeInTheDocument();
    expect(screen.queryByText("读书笔记")).not.toBeInTheDocument();
  });

  it.each(["running", "paused", "incomplete"] as const)("%s 探索显示行动、追问和失败原因", (status) => {
    const exploration: Activity = {
      ...result("explore", "free_exploration", "", ""), status,
      progress: {
        description: "已搜索具体问题", intent: "最初问题",
        exploration: {
          actions: [
            { name: "select_topic", text: "最初问题", ok: true },
            { name: "follow_up", text: "证据还有什么缺口", ok: true },
            { name: "web_fetch", args: { url: "https://example.com/source" }, ok: false, reason: "403" },
          ],
          failure_reason: "正文抓取失败", unresolved_questions: ["尚待核实的问题"],
        },
      },
    };
    useActivityStore.setState({ data: { current: status === "running" ? exploration : null, schedule: [exploration] } });
    render(<ActivityPanel />);
    fireEvent.click(screen.getByText("探索过程"));
    expect(screen.getByText("已搜索具体问题")).toBeInTheDocument();
    expect(screen.getByText(/证据还有什么缺口/)).toHaveTextContent("https://example.com/source");
    expect(screen.getByText(/正文抓取失败/)).toHaveTextContent("尚待核实的问题");
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

    render(<ActivityPanel view="tasks" />);
    fireEvent.click(screen.getByRole("tab", { name: "安排任务" }));

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

    render(<ActivityPanel view="tasks" />);

    expect(screen.getByText("失败")).toBeInTheDocument();
    expect(screen.getByText("正文抓取失败")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "查看任务 https://example.com/article" }));
    expect(screen.getByLabelText("任务详情")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "关闭任务详情" }));
    expect(screen.queryByLabelText("任务详情")).not.toBeInTheDocument();
  });

  it("网页问卷可提交，完成后显示结果", async () => {
    const create = vi.spyOn(useActivityStore.getState(), "createBrowserSurveyTask")
      .mockResolvedValue(true);
    useActivityStore.setState({
      tasks: [{
        id: "survey", type: "browser_survey", status: "completed",
        url: "https://example.com/quiz", book_id: null,
        target_paragraph: null,
        checkpoint: { result: { summary: "我的结果" } },
        error: null, created_at: 1, updated_at: 2,
      }],
    });
    render(<ActivityPanel view="tasks" />);
    expect(screen.queryByText("我的结果")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "查看任务 网页问卷 · https://example.com/quiz" }));
    fireEvent.click(screen.getByRole("tab", { name: "安排任务" }));
    fireEvent.change(screen.getAllByPlaceholderText("https://")[1], {
      target: { value: "https://example.com/quiz" },
    });
    fireEvent.click(screen.getByRole("button", { name: "安排问卷" }));
    await waitFor(() => expect(create).toHaveBeenCalledWith("https://example.com/quiz"));
    fireEvent.click(screen.getByRole("tab", { name: "任务记录" }));
    fireEvent.click(screen.getByRole("button", { name: "查看任务 网页问卷 · https://example.com/quiz" }));
    expect(screen.getByText("我的结果")).toBeInTheDocument();
  });
});
