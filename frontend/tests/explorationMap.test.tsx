import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import ExplorationMap from "../src/components/exploration/ExplorationMap";
import { useActivityStore } from "../src/stores/activityStore";
import { useExplorationStore } from "../src/stores/explorationStore";

beforeEach(() => {
  useActivityStore.setState({
    data: null,
    results: [
      {
        id: "a1", type: "free_exploration", schedule_block_id: "b1",
        status: "completed", started_at: 1, ended_at: 2,
        progress: {
          result: {
            findings: ["深海鱼会发光"],
            nodes: [{ name: "搜索：深海鱼", url: "", kind: "search" }],
          },
        },
      },
    ],
    error: null,
  });
  useExplorationStore.setState({ wishlist: ["发光生物"], liveNodes: [], activityId: null });
});

describe("ExplorationMap", () => {
  it("渲染历史节点 + 心愿单", () => {
    render(<ExplorationMap onClose={() => {}} />);
    expect(screen.getByText("搜索：深海鱼")).toBeTruthy();
    expect(screen.getByText("发光生物")).toBeTruthy();
  });

  it("「出门探索」点击调 start（POST /api/explore）", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ activity_id: "e1" }) });
    vi.stubGlobal("fetch", fetchMock);
    render(<ExplorationMap onClose={() => {}} />);
    fireEvent.click(screen.getByText("出门探索"));
    expect(fetchMock).toHaveBeenCalledWith("/api/explore", expect.objectContaining({ method: "POST" }));
    await waitFor(() => expect(useExplorationStore.getState().activityId).toBe("e1"));
    vi.unstubAllGlobals();
  });

  it("「出门探索」忙碌(409) → 显示错误", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: false,
      status: 409,
      json: async () => ({ detail: "已有活动进行中" }),
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<ExplorationMap onClose={() => {}} />);
    fireEvent.click(screen.getByText("出门探索"));
    expect(await screen.findByText("已有活动进行中")).toBeTruthy();
    vi.unstubAllGlobals();
  });

  it("「＋」加心愿调用 addWish", () => {
    render(<ExplorationMap onClose={() => {}} />);
    fireEvent.change(screen.getByPlaceholderText("加一个想探索的主题"), {
      target: { value: "深海鱼" },
    });
    fireEvent.click(screen.getByText("＋"));
    expect(useExplorationStore.getState().wishlist).toContain("深海鱼");
  });
});
