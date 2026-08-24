import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import ReadingNotesPanel from "../src/components/panels/ReadingNotesPanel";
import { useReadingNotesStore } from "../src/stores/readingNotesStore";
import type { Annotation, ReadingNote } from "../src/types/api";

function jsonResponse(body: unknown, ok = true, status = 200): Response {
  return { ok, status, json: async () => body } as Response;
}

const note: ReadingNote = {
  id: "n1",
  book: "三体",
  content: "这是笔记正文",
  created_at: 1,
  annotation_count: 0,
  path: "",
};

const anno: Annotation = {
  id: "a1",
  target_id: "n1",
  author: "user",
  content: "我的批注",
  created_at: 2,
};

afterEach(() => {
  vi.unstubAllGlobals();
});

beforeEach(() => {
  useReadingNotesStore.setState({ notes: null, loading: false, error: null });
});

describe("ReadingNotesPanel 读书笔记面板", () => {
  it("渲染笔记清单：书名 + 内容预览 + 批注数徽标", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue(jsonResponse([{ ...note, annotation_count: 3 }]));
    vi.stubGlobal("fetch", fetchMock);

    render(<ReadingNotesPanel />);

    expect(await screen.findByText("《三体》")).toBeInTheDocument();
    expect(screen.getByText("💬3")).toBeInTheDocument();
    expect(screen.getByText("这是笔记正文")).toBeInTheDocument();
    expect(fetchMock.mock.calls[0][0]).toBe("/api/reading-notes?limit=50");
  });

  it("点卡片展开详情：显示 Markdown 正文 + 返回按钮 + 批注", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse([note])) // refresh 清单
      .mockResolvedValueOnce(jsonResponse([anno])); // openNote → getAnnotations
    vi.stubGlobal("fetch", fetchMock);

    render(<ReadingNotesPanel />);
    fireEvent.click(await screen.findByText("《三体》"));

    expect(await screen.findByText("我的批注")).toBeInTheDocument();
    expect(screen.getByText("这是笔记正文")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "← 返回" })).toBeInTheDocument();
  });

  it("新增批注：POST /api/annotations 后重新拉取并显示", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse([note])) // refresh 清单
      .mockResolvedValueOnce(jsonResponse([])) // openNote → 空批注
      .mockResolvedValueOnce(jsonResponse(anno)) // addAnnotation POST
      .mockResolvedValueOnce(jsonResponse([anno])) // 重拉批注
      .mockResolvedValueOnce(
        jsonResponse([{ ...note, annotation_count: 1 }]),
      ); // 增批注后 refresh 清单（徽标 +1）
    vi.stubGlobal("fetch", fetchMock);

    render(<ReadingNotesPanel />);
    fireEvent.click(await screen.findByText("《三体》"));
    expect(await screen.findByText("暂无批注")).toBeInTheDocument();

    fireEvent.change(screen.getByPlaceholderText("添加你的批注……"), {
      target: { value: "我的批注" },
    });
    fireEvent.click(screen.getByRole("button", { name: "添加批注" }));

    expect(await screen.findByText("我的批注")).toBeInTheDocument();
    const postCall = fetchMock.mock.calls.find(
      ([, init]) => init?.method === "POST",
    );
    expect(postCall?.[0]).toBe("/api/annotations");
    expect(JSON.parse(String(postCall?.[1]?.body))).toEqual({
      target_id: "n1",
      content: "我的批注",
    });
    // 增批注后又拉了一次清单（refresh）→ annotation_count 徽标不再陈旧
    const listCalls = fetchMock.mock.calls.filter(
      ([url]) => url === "/api/reading-notes?limit=50",
    );
    expect(listCalls).toHaveLength(2);
  });

  it("删除批注：DELETE /api/annotations/{id} 后从列表摘除", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse([note]))
      .mockResolvedValueOnce(jsonResponse([anno]))
      .mockResolvedValueOnce(jsonResponse({ deleted: "a1" }))
      .mockResolvedValueOnce(jsonResponse([note])); // 删批注后 refresh 清单
    vi.stubGlobal("fetch", fetchMock);

    render(<ReadingNotesPanel />);
    fireEvent.click(await screen.findByText("《三体》"));
    fireEvent.click(await screen.findByRole("button", { name: "删除" }));

    await waitFor(() =>
      expect(screen.queryByText("我的批注")).not.toBeInTheDocument(),
    );
    const delCall = fetchMock.mock.calls.find(
      ([url, init]) => url === "/api/annotations/a1" && init?.method === "DELETE",
    );
    expect(delCall).toBeTruthy();
    // 删批注后又拉了一次清单（refresh）→ annotation_count 徽标不再陈旧
    const listCalls = fetchMock.mock.calls.filter(
      ([url]) => url === "/api/reading-notes?limit=50",
    );
    expect(listCalls).toHaveLength(2);
  });

  it("删除笔记：confirm 后 DELETE /api/reading-notes/{id}，清单摘除", async () => {
    vi.stubGlobal("confirm", vi.fn(() => true));
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse([note]))
      .mockResolvedValueOnce(jsonResponse({ deleted: "n1" }));
    vi.stubGlobal("fetch", fetchMock);

    render(<ReadingNotesPanel />);
    fireEvent.click(await screen.findByRole("button", { name: "🗑 删除" }));

    await waitFor(() =>
      expect(screen.queryByText("《三体》")).not.toBeInTheDocument(),
    );
    const delCall = fetchMock.mock.calls.find(
      ([url, init]) =>
        url === "/api/reading-notes/n1" && init?.method === "DELETE",
    );
    expect(delCall).toBeTruthy();
  });

  it("切换笔记 A→B 丢弃 A 的陈旧批注响应（竞态守卫）", async () => {
    const noteB: ReadingNote = {
      id: "n2",
      book: "红楼梦",
      content: "B 的正文",
      created_at: 3,
      annotation_count: 0,
      path: "",
    };
    const annoA: Annotation = {
      id: "aA",
      target_id: "n1",
      author: "user",
      content: "A 的批注",
      created_at: 4,
    };
    const annoB: Annotation = {
      id: "aB",
      target_id: "n2",
      author: "user",
      content: "B 的批注",
      created_at: 5,
    };

    // A 的批注手动延迟 resolve，模拟「A 请求晚于 B 返回」的竞态
    let resolveA!: (v: Response) => void;
    const deferredA = new Promise<Response>((r) => {
      resolveA = r;
    });

    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse([note, noteB])) // refresh 清单
      .mockImplementationOnce(() => deferredA) // openNote(A) → getAnnotations(A)，慢
      .mockResolvedValueOnce(jsonResponse([annoB])); // openNote(B) → getAnnotations(B)，快
    vi.stubGlobal("fetch", fetchMock);

    render(<ReadingNotesPanel />);
    fireEvent.click(await screen.findByText("《三体》")); // 打开 A
    fireEvent.click(await screen.findByRole("button", { name: "← 返回" })); // 回列表
    fireEvent.click(await screen.findByText("《红楼梦》")); // 打开 B

    // B 的批注先到并显示
    expect(await screen.findByText("B 的批注")).toBeInTheDocument();
    expect(screen.getByText("B 的正文")).toBeInTheDocument();

    // A 的陈旧响应后到，被序号守卫丢弃，界面仍是 B 的批注
    resolveA(jsonResponse([annoA]));
    await waitFor(() =>
      expect(screen.queryByText("A 的批注")).not.toBeInTheDocument(),
    );
    expect(screen.getByText("B 的批注")).toBeInTheDocument();
  });
});
