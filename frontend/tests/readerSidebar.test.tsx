import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import ReaderSidebar from "../src/components/reading/ReaderSidebar";
import { useReaderStore } from "../src/stores/readerStore";

beforeEach(() => {
  useReaderStore.setState({
    bookId: "b1",
    userPosition: 10,
    nyxPosition: 5,
    impulseBubbles: [],
    notes: [],
    notesError: null,
  });
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("ReaderSidebar 冲动气泡", () => {
  it("渲染三态气泡（kind 决定样式类）", () => {
    useReaderStore.setState({
      impulseBubbles: [
        { id: "e1", kind: "mutter", bookId: "b1", paragraphIndex: 2, content: "妙" },
        {
          id: "e2",
          kind: "question",
          bookId: "b1",
          paragraphIndex: 3,
          content: "为什么？",
          subtype: "question_reflective",
          selectedText: null,
        },
        {
          id: "e3",
          kind: "association",
          bookId: "b1",
          paragraphIndex: 4,
          content: "片段",
          memoryId: "m1",
        },
      ],
    });

    render(<ReaderSidebar />);

    expect(screen.getByText("妙")).toBeInTheDocument();
    expect(screen.getByText("为什么？")).toBeInTheDocument();
    expect(screen.getByText("片段")).toBeInTheDocument();

    const bubbles = Array.from(document.querySelectorAll(".reader-bubble"));
    expect(bubbles.map((b) => b.className)).toEqual([
      "reader-bubble reader-bubble--mutter",
      "reader-bubble reader-bubble--question",
      "reader-bubble reader-bubble--association",
    ]);
  });

  it("点「笔记」打开 NotePanel（挂载即 loadNotes）", () => {
    const loadSpy = vi
      .spyOn(useReaderStore.getState(), "loadNotes")
      .mockResolvedValue(undefined);

    render(<ReaderSidebar />);
    fireEvent.click(screen.getByRole("button", { name: "笔记" }));

    expect(screen.getByRole("dialog", { name: "笔记" })).toBeInTheDocument();
    expect(loadSpy).toHaveBeenCalledTimes(1);
  });
});
