import { render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import ReaderView from "../src/components/reading/ReaderView";
import { useReaderStore } from "../src/stores/readerStore";
import type { Paragraph } from "../src/types/api";

const para = (index: number, text: string): Paragraph => ({
  id: `p${index}`,
  book_id: "b1",
  index,
  text,
  is_chapter_start: index === 1,
});

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
    expect(screen.getByRole("button", { name: "笔记" })).toBeInTheDocument(); // 笔记入口在 footer
    const pos = document.querySelector(".reader__pos")?.textContent;
    expect(pos).toContain("她读到第 5 段");
    expect(pos).toContain("你读到第 3");
  });
});
