import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import MemoryPanel from "../src/components/panels/MemoryPanel";
import { useMemoryStore } from "../src/stores/memoryStore";

describe("MemoryPanel", () => {
  beforeEach(() => {
    useMemoryStore.setState({
      data: [],
      facts: [],
      query: "",
      error: null,
      factsError: null,
      refresh: vi.fn().mockResolvedValue(undefined),
      search: vi.fn().mockResolvedValue(undefined),
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("关键词查询：提交后调用 store search", async () => {
    const search = vi.fn().mockResolvedValue(undefined);
    useMemoryStore.setState({ search });
    render(<MemoryPanel />);

    fireEvent.change(screen.getByRole("textbox", { name: "记忆关键词" }), {
      target: { value: "猫" },
    });
    fireEvent.click(screen.getByRole("button", { name: "查询" }));

    await waitFor(() => expect(search).toHaveBeenCalledWith("猫"));
  });

  it("事实图：用实体节点和关系边展示当前事实", () => {
    useMemoryStore.setState({
      facts: [
        {
          id: "f1",
          subject: "尼克斯",
          subject_type: "agent",
          predicate: "喜欢",
          object_value: "古典文学",
          object_type: "concept",
          valid_from: 100,
          valid_until: null,
          source_memory_id: "m1",
          created_at: 100,
          polarity: 1,
        },
      ],
    });
    render(<MemoryPanel />);

    fireEvent.click(screen.getByRole("tab", { name: "事实图" }));

    expect(screen.getByRole("img", { name: /事实关系图/ })).toBeInTheDocument();
    expect(screen.getByText("尼克斯")).toBeInTheDocument();
    expect(screen.getByText("古典文学")).toBeInTheDocument();
    expect(screen.getByText("喜欢")).toBeInTheDocument();
  });

  it("事实加载失败：图区域显示错误但面板仍可切换", () => {
    useMemoryStore.setState({ facts: null, factsError: "事实暂时无法加载" });
    render(<MemoryPanel />);

    fireEvent.click(screen.getByRole("tab", { name: "事实图" }));

    expect(screen.getByText("事实暂时无法加载")).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "记忆" })).toBeInTheDocument();
  });
});
