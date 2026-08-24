import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import MemoryPanel from "../src/components/panels/MemoryPanel";
import { useMemoryStore } from "../src/stores/memoryStore";
import type { Memory } from "../src/types/api";

function jsonResponse(body: unknown, ok = true, status = 200): Response {
  return { ok, status, json: async () => body } as Response;
}

const memA: Memory = {
  id: "m1",
  created_at: 100,
  content: "完整内容A",
  tag: "cat",
  summary: "摘要A",
  freshness: 0.5,
  type: "short_term",
  recall_count: 1,
  aspect: [],
  embedding: null,
};

const memB: Memory = {
  id: "m2",
  created_at: 200,
  content: "完整内容B",
  tag: "user",
  summary: "摘要B",
  freshness: 0.9,
  type: "long_term",
  recall_count: 5,
  aspect: [],
  embedding: null,
};

afterEach(() => {
  vi.unstubAllGlobals();
});

beforeEach(() => {
  useMemoryStore.setState({ data: null, error: null });
});

describe("MemoryPanel 记忆面板", () => {
  it("渲染清单：摘要 + 召回次数 + 时间", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse([memA])));
    render(<MemoryPanel />);

    expect(await screen.findByText("摘要A")).toBeInTheDocument();
    expect(screen.getByText(/召回×1/)).toBeInTheDocument();
    expect(screen.getByText(/\d{4}-\d{2}-\d{2}/)).toBeInTheDocument();
  });

  it("输入搜索词 → 调后端语义搜索并替换列表", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse([memA, memB])) // refresh 全量
      .mockResolvedValueOnce(jsonResponse([memB])); // search 只返回 memB
    vi.stubGlobal("fetch", fetchMock);

    render(<MemoryPanel />);
    await screen.findByText("摘要A");

    fireEvent.change(screen.getByPlaceholderText("搜索记忆…"), {
      target: { value: "猫" },
    });

    await waitFor(() => {
      const urls = fetchMock.mock.calls.map((c) => c[0]);
      expect(urls).toContain("/api/memories/search?q=%E7%8C%AB");
    });
    await waitFor(() =>
      expect(screen.queryByText("摘要A")).not.toBeInTheDocument(),
    );
    expect(screen.getByText("摘要B")).toBeInTheDocument();
  });

  it("tag 筛选：只显示匹配标签", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse([memA, memB])));
    render(<MemoryPanel />);
    await screen.findByText("摘要A");

    fireEvent.change(screen.getByRole("combobox", { name: "标签筛选" }), {
      target: { value: "user" },
    });

    expect(screen.queryByText("摘要A")).not.toBeInTheDocument();
    expect(screen.getByText("摘要B")).toBeInTheDocument();
  });

  it("类型筛选：只显示匹配类型", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse([memA, memB])));
    render(<MemoryPanel />);
    await screen.findByText("摘要A");

    fireEvent.change(screen.getByRole("combobox", { name: "类型筛选" }), {
      target: { value: "long_term" },
    });

    expect(screen.queryByText("摘要A")).not.toBeInTheDocument();
    expect(screen.getByText("摘要B")).toBeInTheDocument();
  });

  it("排序：按召回次数降序", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse([memA, memB])));
    render(<MemoryPanel />);
    await screen.findByText("摘要A");

    fireEvent.change(screen.getByRole("combobox", { name: "排序" }), {
      target: { value: "recall" },
    });

    const items = screen.getAllByRole("listitem");
    expect(items[0].textContent).toContain("摘要B");
    expect(items[1].textContent).toContain("摘要A");
  });

  it("点击展开完整内容", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse([memA])));
    render(<MemoryPanel />);
    await screen.findByText("摘要A");

    expect(screen.queryByText("完整内容A")).not.toBeInTheDocument();
    fireEvent.click(screen.getByText("摘要A"));
    expect(screen.getByText("完整内容A")).toBeInTheDocument();
  });
});
