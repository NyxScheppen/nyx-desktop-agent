import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import ReaderView, { renderRange } from "../src/components/reading/ReaderView";
import { useReaderStore } from "../src/stores/readerStore";
import type { Paragraph } from "../src/types/api";

const para = (index: number, text: string): Paragraph => ({
  id: `p${index}`,
  book_id: "b1",
  index,
  text,
  is_chapter_start: index === 1,
});

function selectRange(
  startElement: Element,
  startOffset: number,
  endElement: Element,
  endOffset: number,
): void {
  const startNode = startElement.firstChild;
  const endNode = endElement.firstChild;
  const selection = window.getSelection();
  if (startNode === null || endNode === null || selection === null) {
    throw new Error("测试选区缺少文本节点");
  }
  const range = document.createRange();
  range.setStart(startNode, startOffset);
  range.setEnd(endNode, endOffset);
  Object.defineProperty(range, "getBoundingClientRect", {
    value: () => ({ left: 12, top: 48 }) as DOMRect,
  });
  selection.removeAllRanges();
  selection.addRange(range);
}

describe("ReaderView 位置高亮（真分页）", () => {
  beforeEach(() => {
    // jsdom 无 ResizeObserver：stub 空实现（viewportHeight 保持 0，分页返回空页，不影响类名断言）
    vi.stubGlobal(
      "ResizeObserver",
      class {
        observe() {}
        disconnect() {}
        unobserve() {}
      },
    );
    useReaderStore.setState({
      books: [],
      bookId: "b1",
      totalParagraphs: 6,
      paragraphs: [
        para(1, "一"),
        para(2, "二"),
        para(3, "三"),
        para(4, "四"),
        para(5, "五"),
        para(6, "六"),
      ],
      userPosition: 3,
      nyxPosition: 5,
      readCount: 0,
      notes: [],
      notesError: null,
      bookmarks: [],
      bookmarksError: null,
    });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("当前段加 --current、Nyx 段加 --nyx，其余段无", () => {
    const { container } = render(<ReaderView />);
    const paras = Array.from(container.querySelectorAll(".reader-text__para"));
    const byText = (t: string) => paras.find((p) => p.textContent === t);

    expect(byText("三")?.className).toContain("reader-text__para--current");
    expect(byText("三")?.className).not.toContain("reader-text__para--nyx");
    expect(byText("五")?.className).toContain("reader-text__para--nyx");
    expect(byText("五")?.className).not.toContain("reader-text__para--current");
    expect(byText("二")?.className).not.toContain("reader-text__para--current");
    expect(byText("二")?.className).not.toContain("reader-text__para--nyx");
  });

  it("侧栏已拆：笔记入口在 footer、header 显示她/你读到第几段", () => {
    render(<ReaderView />);
    expect(document.querySelector(".reader-sidebar")).toBeNull(); // 侧栏已拆
    expect(screen.getByRole("button", { name: "上一页" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "下一页" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "笔记" })).toBeInTheDocument(); // 笔记入口在 footer
    const pos = document.querySelector(".reader__pos")?.textContent;
    expect(pos).toContain("她在等你");
    expect(pos).toContain("她读到第 5 段");
    expect(pos).toContain("你读到第 3");
  });

  it("按结构渲染标题、加粗和已有划线", () => {
    useReaderStore.setState({
      paragraphs: [{
        ...para(1, "标题\n正文加粗"),
        blocks: [
          { kind: "heading", start: 0, end: 2, level: 2 },
          { kind: "paragraph", start: 3, end: 7, level: null },
        ],
        marks: [{ start: 5, end: 7, bold: true, italic: false }],
      }],
      notes: [{
        id: "n1",
        book_id: "b1",
        paragraph_id: "p1",
        paragraph_index: 1,
        content: "",
        selected_text: "正文",
        selection_start: 3,
        selection_end: 5,
        created_at: 1,
        updated_at: 1,
        annotations: [],
      }],
      userPosition: 1,
      nyxPosition: 1,
    });

    const { container } = render(<ReaderView />);

    expect(screen.getByRole("heading", { name: "标题" })).toBeInTheDocument();
    expect(screen.getByText("加粗")).toHaveClass("reader-run--bold");
    expect(screen.getByText("正文")).toHaveClass("reader-run--highlight");
    expect(container.querySelector(".reader-text__para")).toHaveAttribute(
      "data-paragraph-id",
      "p1",
    );
  });

  it("重叠划线分段不会反复读取全部划线端点", () => {
    let offsetReads = 0;
    const notes = Array.from({ length: 100 }, (_, index) => {
      const note = {
        id: `n${index}`,
        book_id: "b1",
        paragraph_id: "p1",
        paragraph_index: 1,
        content: "",
        selected_text: "x",
        created_at: index,
        updated_at: index,
        annotations: [],
      };
      Object.defineProperties(note, {
        selection_start: {
          get: () => {
            offsetReads += 1;
            return index;
          },
        },
        selection_end: {
          get: () => {
            offsetReads += 1;
            return 200 - index;
          },
        },
      });
      return note;
    });

    const nodes = renderRange(
      para(1, "x".repeat(200)),
      0,
      200,
      [],
      notes,
      null,
    );

    expect(nodes.length).toBeGreaterThan(100);
    expect(offsetReads).toBeLessThanOrEqual(notes.length * 4);
  });

  it("重叠划线扫描保留格式和聚焦状态", () => {
    const baseNote = {
      book_id: "b1",
      paragraph_id: "p1",
      paragraph_index: 1,
      content: "",
      created_at: 1,
      updated_at: 1,
      annotations: [],
    };
    const nodes = renderRange(
      para(1, "abcdef"),
      0,
      6,
      [{ start: 1, end: 3, bold: false, italic: true }],
      [
        { ...baseNote, id: "n1", selected_text: "abcd", selection_start: 0, selection_end: 4 },
        { ...baseNote, id: "n2", selected_text: "cdef", selection_start: 2, selection_end: 6 },
      ],
      "n2",
    );
    const { container } = render(<>{nodes}</>);

    expect(Array.from(container.querySelectorAll("span")).map((node) => ({
      text: node.textContent,
      classes: node.className,
    }))).toEqual([
      { text: "a", classes: "reader-run reader-run--highlight" },
      { text: "b", classes: "reader-run reader-run--italic reader-run--highlight" },
      { text: "c", classes: "reader-run reader-run--italic reader-run--highlight reader-run--focused" },
      { text: "d", classes: "reader-run reader-run--highlight reader-run--focused" },
      { text: "ef", classes: "reader-run reader-run--highlight reader-run--focused" },
    ]);
  });

  it("划线搜索过滤结果，点击结果调用持久定位", () => {
    const jump = vi.fn().mockResolvedValue(undefined);
    useReaderStore.setState({
      jumpToPosition: jump,
      notes: [
        {
          id: "n1",
          book_id: "b1",
          paragraph_id: "p2",
          paragraph_index: 2,
          content: "",
          selected_text: "月光落在窗台",
          selection_start: 0,
          selection_end: 7,
          created_at: 1,
          updated_at: 1,
          annotations: [],
        },
        {
          id: "n2",
          book_id: "b1",
          paragraph_id: "p4",
          paragraph_index: 4,
          content: "",
          selected_text: "雨声",
          selection_start: 0,
          selection_end: 2,
          created_at: 2,
          updated_at: 2,
          annotations: [],
        },
      ],
    });
    render(<ReaderView />);

    fireEvent.click(screen.getByRole("button", { name: "划线" }));
    fireEvent.change(screen.getByRole("searchbox", { name: "搜索划线" }), {
      target: { value: "月光" },
    });
    expect(screen.getByText("月光落在窗台")).toBeInTheDocument();
    expect(screen.queryByText("雨声")).not.toBeInTheDocument();
    fireEvent.click(screen.getByText("月光落在窗台"));

    expect(jump).toHaveBeenCalledWith(2);
  });

  it("同段跨格式选择按 UTF-16 offset 保存划线", async () => {
    const addNote = vi.fn().mockResolvedValue(undefined);
    useReaderStore.setState({
      addNote,
      paragraphs: [{
        ...para(1, "甲😀乙丙"),
        blocks: [{ kind: "paragraph", start: 0, end: 5, level: null }],
        marks: [{ start: 1, end: 3, bold: true, italic: false }],
      }],
      userPosition: 1,
      nyxPosition: 1,
    });
    const { container } = render(<ReaderView />);

    selectRange(screen.getByText("😀"), 0, screen.getByText("乙丙"), 1);
    fireEvent.mouseUp(container.querySelector(".reader-text") as Element);
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "保存划线" }));
    });

    expect(addNote).toHaveBeenCalledWith({
      book_id: "b1",
      paragraph_id: "p1",
      content: "",
      selected_text: "😀乙",
      selection_start: 1,
      selection_end: 4,
    });
  });

  it("跨段选择不显示保存划线操作", () => {
    const { container } = render(<ReaderView />);
    selectRange(screen.getByText("一"), 0, screen.getByText("二"), 1);

    fireEvent.mouseUp(container.querySelector(".reader-text") as Element);

    expect(
      screen.queryByRole("button", { name: "保存划线" }),
    ).not.toBeInTheDocument();
  });

  it("书签列表点击调用持久定位", () => {
    const jump = vi.fn().mockResolvedValue(undefined);
    useReaderStore.setState({
      jumpToPosition: jump,
      bookmarks: [{
        id: "bm1",
        book_id: "b1",
        paragraph_id: "p4",
        paragraph_index: 4,
        preview: "远处的第四段",
        created_at: 1,
      }],
    });
    render(<ReaderView />);

    fireEvent.click(screen.getByRole("button", { name: "书签" }));
    fireEvent.click(screen.getByText("远处的第四段"));

    expect(jump).toHaveBeenCalledWith(4);
  });
});
