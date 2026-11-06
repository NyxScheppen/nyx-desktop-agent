import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import MemoryPanel from "../src/components/panels/MemoryPanel";
import { useMemoryStore } from "../src/stores/memoryStore";

describe("MemoryPanel", () => {
  beforeEach(() => {
    useMemoryStore.setState({
      data: [],
      facts: [],
      conversations: [],
      conversationsError: null,
      conversationsMore: false,
      conversationsLoading: false,
      query: "",
      kind: null,
      factScope: "profile",
      error: null,
      factsError: null,
      refresh: vi.fn().mockResolvedValue(undefined),
      search: vi.fn().mockResolvedValue(undefined),
      selectFactScope: vi.fn().mockResolvedValue(undefined),
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("普通记忆先排除知识条目再分页，知识库只显示知识", () => {
    const memories = Array.from({ length: 6 }, (_, index) => ({
      id: `m${index}`, created_at: 1, content: `经历正文${index}`,
      kind: "episode" as const, topics: [], summary: `经历${index}`,
      freshness: 1, type: "long_term" as const, recall_count: 0, sources: [],
    }));
    const knowledge = { ...memories[0], id: "k1", kind: "knowledge" as const,
      summary: "知识标题", content: "知识正文" };
    useMemoryStore.setState({ data: [knowledge, ...memories] });
    render(<MemoryPanel />);

    expect(screen.queryByText("知识标题")).not.toBeInTheDocument();
    expect(screen.getAllByRole("listitem")).toHaveLength(5);
    fireEvent.click(screen.getByRole("button", { name: "下一页" }));
    expect(screen.getAllByRole("listitem")).toHaveLength(1);
    expect(screen.getByText("经历5")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("tab", { name: "知识库" }));
    expect(screen.getAllByRole("listitem")).toHaveLength(1);
    expect(screen.getByText("知识标题")).toBeInTheDocument();
    act(() => useMemoryStore.setState({ data: [knowledge] }));
    fireEvent.click(screen.getByRole("tab", { name: "普通记忆" }));
    expect(screen.getByText("暂无记忆")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "下一页" })).not.toBeInTheDocument();
  });

  it("普通记忆关键词查询：提交后调用 store search", async () => {
    const search = vi.fn().mockResolvedValue(undefined);
    useMemoryStore.setState({ search });
    render(<MemoryPanel />);

    fireEvent.change(screen.getByRole("textbox", { name: "记忆关键词" }), {
      target: { value: "猫" },
    });
    fireEvent.click(screen.getByRole("button", { name: "查询" }));

    await waitFor(() => expect(search).toHaveBeenCalledWith("猫", null));
  });

  it("事实图：用实体节点和关系边展示当前画像事实", () => {
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
          scope: "profile",
          source_segment_id: null, source_chunk_id: null,
        },
      ],
    });
    render(<MemoryPanel />);

    fireEvent.click(screen.getByRole("tab", { name: "画像事实" }));

    expect(screen.getByRole("img", { name: /事实关系图/ })).toBeInTheDocument();
    expect(screen.getByText("尼克斯")).toBeInTheDocument();
    expect(screen.getByText("古典文学")).toBeInTheDocument();
    expect(screen.getByText("喜欢")).toBeInTheDocument();
  });

  it("知识库模式固定知识检索并提供事实网", async () => {
    const search = vi.fn().mockResolvedValue(undefined);
    const selectFactScope = vi.fn().mockResolvedValue(undefined);
    useMemoryStore.setState({ search, selectFactScope });
    render(<MemoryPanel />);

    fireEvent.click(screen.getByRole("tab", { name: "知识库" }));
    expect(search).toHaveBeenCalledWith("", "knowledge");
    expect(screen.queryByRole("combobox", { name: "记忆类别" })).not.toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "事实网" })).toBeInTheDocument();

    fireEvent.click(screen.getByRole("tab", { name: "事实网" }));
    expect(selectFactScope).toHaveBeenCalledWith("knowledge");
    fireEvent.click(screen.getByRole("tab", { name: "知识条目" }));

    fireEvent.change(screen.getByRole("textbox", { name: "知识库关键词" }), {
      target: { value: "历史" },
    });
    fireEvent.click(screen.getByRole("button", { name: "查询" }));
    expect(search).toHaveBeenLastCalledWith("历史", "knowledge");
  });

  it("切回普通记忆保持统一搜索，不显示类别筛选", async () => {
    const search = vi.fn().mockResolvedValue(undefined);
    useMemoryStore.setState({ search });
    render(<MemoryPanel />);

    fireEvent.click(screen.getByRole("tab", { name: "知识库" }));
    fireEvent.click(screen.getByRole("tab", { name: "普通记忆" }));

    expect(search).toHaveBeenLastCalledWith("", null);
    expect(screen.queryByRole("combobox", { name: "记忆类别" })).not.toBeInTheDocument();
  });

  it("事实加载失败：图区域显示错误但面板仍可切换", () => {
    useMemoryStore.setState({ facts: null, factsError: "事实暂时无法加载" });
    render(<MemoryPanel />);

    fireEvent.click(screen.getByRole("tab", { name: "画像事实" }));

    expect(screen.getByText("事实暂时无法加载")).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "普通记忆" })).toBeInTheDocument();
  });

  it("角色重置事实作用域后，画像事实标签跟随当前图", () => {
    const selectFactScope = vi.fn().mockResolvedValue(undefined);
    useMemoryStore.setState({ factScope: "conversation", selectFactScope });
    render(<MemoryPanel />);
    fireEvent.click(screen.getByRole("tab", { name: "对话事实" }));
    expect(screen.getByRole("tab", { name: "对话事实" })).toHaveAttribute("aria-selected", "true");

    act(() => useMemoryStore.setState({ factScope: "profile", facts: [] }));
    expect(screen.getByRole("tab", { name: "画像事实" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("tab", { name: "对话事实" })).toHaveAttribute("aria-selected", "false");
  });
  it("对话原文与内在反思可以独立筛选", () => {
    useMemoryStore.setState({
      conversations: [{ id: "dialogue", started_at: 1, ended_at: 2,
        raw_content: "用户：具体原话\n尼克斯：具体回复", summary: "对话摘要",
        long_term: true, active: true, recall_count: 0 }],
      data: [{ id: "reflection", created_at: 3, kind: "reflection", summary: "内在反思标题",
        content: "反思输入与状态变化", topics: [], freshness: 1, type: "long_term", recall_count: 0, sources: [] }],
    });
    render(<MemoryPanel />);
    fireEvent.click(screen.getByRole("button", { name: /^对话$/ }));
    expect(screen.getByText("对话摘要")).toBeInTheDocument();
    expect(screen.queryByText("内在反思标题")).not.toBeInTheDocument();
    fireEvent.click(screen.getByText("查看完整原文"));
    expect(screen.getByText(/用户：具体原话/)).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: /^内在反思$/ }));
    expect(screen.getByText("内在反思标题")).toBeInTheDocument();
    expect(screen.queryByText("对话摘要")).not.toBeInTheDocument();
  });

  it("事实节点可用方向键移动，缩放和重置保持可用", () => {
    useMemoryStore.setState({ facts: [{ id: "f", subject: "尼克斯", subject_type: "agent",
      predicate: "阅读", object_value: "诗经", object_type: "book", valid_from: 1,
      valid_until: null, source_memory_id: "m1", created_at: 1, polarity: 1,
      scope: "knowledge", source_segment_id: null, source_chunk_id: null }] });
    render(<MemoryPanel />);
    fireEvent.click(screen.getByRole("tab", { name: "画像事实" }));
    const node = screen.getByRole("button", { name: "实体：尼克斯" });
    const before = node.getAttribute("transform");
    fireEvent.keyDown(node, { key: "ArrowRight" });
    expect(node.getAttribute("transform")).not.toBe(before);
    fireEvent.click(screen.getByRole("button", { name: "放大事实网" }));
    expect(screen.getByText("125%")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "重置视图" }));
    expect(node.getAttribute("transform")).toBe(before);
    expect(screen.getByText("100%")).toBeInTheDocument();
  });

});
