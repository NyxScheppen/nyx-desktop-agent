import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import NotePanel from "../src/components/reading/NotePanel";
import { useReaderStore } from "../src/stores/readerStore";

beforeEach(() => {
  useReaderStore.setState({ bookId: "b1", notes: [], notesError: null });
  // NotePanel 挂载即 loadNotes → 真 fetch，spy 拦下（store 层已单测，组件只验 wiring）。
  vi.spyOn(useReaderStore.getState(), "loadNotes").mockResolvedValue(undefined);
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("NotePanel 笔记面板", () => {
  it("渲染笔记（content + selected_text + 批注）", () => {
    useReaderStore.setState({
      notes: [
        {
          id: "n1",
          book_id: "b1",
          paragraph_id: "p1",
          content: "第一条笔记",
          selected_text: "划线原文",
          created_at: 1,
          updated_at: 2,
          annotations: [{ id: "a1", user_note_id: "n1", content: "批注", created_at: 3 }],
        },
      ],
    });

    render(<NotePanel onClose={() => {}} />);

    expect(screen.getByText("第一条笔记")).toBeInTheDocument();
    expect(screen.getByText("划线原文")).toBeInTheDocument();
    expect(screen.getByText("批注")).toBeInTheDocument();
  });

  it("composer 提交 → addNote（book_id + content）", () => {
    const addSpy = vi.spyOn(useReaderStore.getState(), "addNote").mockResolvedValue();

    render(<NotePanel onClose={() => {}} />);
    fireEvent.change(screen.getByPlaceholderText("记点什么…"), {
      target: { value: "  新笔记  " },
    });
    fireEvent.click(screen.getByRole("button", { name: "记笔记" }));

    expect(addSpy).toHaveBeenCalledWith({ book_id: "b1", content: "新笔记" });
  });

  it("空白 composer 提交禁用", () => {
    render(<NotePanel onClose={() => {}} />);
    expect(screen.getByRole("button", { name: "记笔记" })).toBeDisabled();
  });

  it("「给尼克斯看」/「删除」调用对应 store action", () => {
    useReaderStore.setState({
      notes: [
        {
          id: "n1",
          book_id: "b1",
          paragraph_id: null,
          content: "c",
          selected_text: null,
          created_at: 1,
          updated_at: 1,
          annotations: [],
        },
      ],
    });
    const showSpy = vi.spyOn(useReaderStore.getState(), "showToNyx").mockResolvedValue();
    const delSpy = vi.spyOn(useReaderStore.getState(), "deleteNote").mockResolvedValue();

    render(<NotePanel onClose={() => {}} />);
    fireEvent.click(screen.getByRole("button", { name: "给尼克斯看" }));
    fireEvent.click(screen.getByRole("button", { name: "删除" }));

    expect(showSpy).toHaveBeenCalledWith("n1");
    expect(delSpy).toHaveBeenCalledWith("n1");
  });

  it("点「编辑」进入编辑态，保存 → updateNote(trim 后内容) + 退出编辑态", () => {
    useReaderStore.setState({
      notes: [
        {
          id: "n1",
          book_id: "b1",
          paragraph_id: null,
          content: "原文",
          selected_text: null,
          created_at: 1,
          updated_at: 1,
          annotations: [],
        },
      ],
    });
    const updSpy = vi.spyOn(useReaderStore.getState(), "updateNote").mockResolvedValue();

    render(<NotePanel onClose={() => {}} />);
    fireEvent.click(screen.getByRole("button", { name: "编辑" }));

    const textarea = screen.getByLabelText("编辑笔记") as HTMLTextAreaElement;
    expect(textarea).toHaveValue("原文");

    fireEvent.change(textarea, { target: { value: "  改后  " } });
    fireEvent.click(screen.getByRole("button", { name: "保存" }));

    expect(updSpy).toHaveBeenCalledWith("n1", "改后");
    expect(screen.getByRole("button", { name: "编辑" })).toBeInTheDocument();
  });

  it("取消 → 退出编辑态、不调 updateNote", () => {
    useReaderStore.setState({
      notes: [
        {
          id: "n1",
          book_id: "b1",
          paragraph_id: null,
          content: "原文",
          selected_text: null,
          created_at: 1,
          updated_at: 1,
          annotations: [],
        },
      ],
    });
    const updSpy = vi.spyOn(useReaderStore.getState(), "updateNote").mockResolvedValue();

    render(<NotePanel onClose={() => {}} />);
    fireEvent.click(screen.getByRole("button", { name: "编辑" }));
    fireEvent.click(screen.getByRole("button", { name: "取消" }));

    expect(updSpy).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "编辑" })).toBeInTheDocument();
  });

  it("编辑态空白 → 保存禁用", () => {
    useReaderStore.setState({
      notes: [
        {
          id: "n1",
          book_id: "b1",
          paragraph_id: null,
          content: "原文",
          selected_text: null,
          created_at: 1,
          updated_at: 1,
          annotations: [],
        },
      ],
    });

    render(<NotePanel onClose={() => {}} />);
    fireEvent.click(screen.getByRole("button", { name: "编辑" }));
    fireEvent.change(screen.getByLabelText("编辑笔记"), { target: { value: "   " } });

    expect(screen.getByRole("button", { name: "保存" })).toBeDisabled();
  });
});
