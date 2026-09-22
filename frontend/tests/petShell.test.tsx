import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import PetShell from "../src/components/desktop/PetShell";
import { useActivityStore } from "../src/stores/activityStore";
import { useReaderStore } from "../src/stores/readerStore";
import { useInnerLifeStore } from "../src/stores/innerLifeStore";
import type { CurrentState } from "../src/types/api";

vi.mock("../src/components/inner/Avatar", () => ({
  default: ({
    children,
    onActivate,
    onDoubleClick,
  }: {
    children: React.ReactNode;
    onActivate?: () => void;
    onDoubleClick?: () => void;
  }) => (
    <div>
      <button type="button" aria-label="Nyx 头像" onClick={onActivate} onDoubleClick={onDoubleClick} />
      {children}
    </div>
  ),
}));

beforeEach(() => {
  useInnerLifeStore.setState({ current: null, error: null });
  useActivityStore.setState({ data: null, results: null, error: null });
  useReaderStore.setState({
    books: [],
    bookId: null,
    paragraphs: [],
    userPosition: 1,
    nyxPosition: 1,
  });
});

describe("PetShell 桌宠菜单", () => {
  it("头像点击在半月菜单展开与收起之间切换", () => {
    render(<PetShell night={false} onExpand={vi.fn()} />);
    const avatar = screen.getByRole("button", { name: "Nyx 头像" });

    fireEvent.click(avatar);
    expect(screen.getByRole("button", { name: "聊天" })).toBeInTheDocument();
    fireEvent.click(avatar);
    expect(screen.queryByRole("button", { name: "聊天" })).not.toBeInTheDocument();
  });

  it("半月菜单凑齐聊天、读书、内心和可修改设置入口", () => {
    const onOpenSettings = vi.fn();
    useInnerLifeStore.setState({
      current: { emotion: "happy", energy: 68, valence: 0.4, arousal: 0.2 } as CurrentState,
      error: null,
    });
    render(<PetShell night={false} onExpand={vi.fn()} onOpenSettings={onOpenSettings} />);
    fireEvent.click(screen.getByRole("button", { name: "Nyx 头像" }));

    for (const label of ["聊天", "读书", "内心", "设置"]) {
      expect(screen.getByRole("button", { name: label })).toBeInTheDocument();
    }

    fireEvent.click(screen.getByRole("button", { name: "内心" }));
    expect(screen.getByRole("region", { name: "内心状态" })).toBeInTheDocument();
    expect(screen.getByText("开心")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "返回上一级" }));
    fireEvent.click(screen.getByRole("button", { name: "设置" }));
    expect(onOpenSettings).toHaveBeenCalledTimes(1);
  });

  it("单击头像展开半月菜单，四项入口保持在同一层级", () => {
    render(<PetShell night={false} onExpand={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: "Nyx 头像" }));
    fireEvent.click(screen.getByRole("button", { name: "聊天" }));
    expect(screen.getByRole("region", { name: "聊天" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "返回上一级" }));
    expect(screen.getByRole("button", { name: "读书" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "设置" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "内心" })).toBeInTheDocument();
  });

  it("头像上方常驻显示状态，精力四舍五入为整数百分比", () => {
    useInnerLifeStore.setState({
      current: { emotion: "happy", energy: 42.6 } as CurrentState,
      error: null,
    });
    render(<PetShell night={false} onExpand={vi.fn()} />);

    expect(screen.getByRole("status", { name: "当前状态" })).toHaveTextContent("精力 43%");
  });

  it("双击头像交给完整桌面端回调", () => {
    const onExpand = vi.fn();
    render(<PetShell night={false} onExpand={onExpand} />);
    fireEvent.doubleClick(screen.getByRole("button", { name: "Nyx 头像" }));
    expect(onExpand).toHaveBeenCalledTimes(1);
  });

  it("读书先选书，陪读二级界面保留返回箭头", async () => {
    const book = {
      id: "book-1",
      title: "诺斯艾兰",
      author: "作者",
      filename: "book.epub",
      total_paragraphs: 10,
      user_position: 2,
      last_read_at: null,
    };
    useReaderStore.setState({
      books: [book],
      openBook: vi.fn(async () => {
        useReaderStore.setState({
          bookId: "book-1",
          paragraphs: [{
            id: "p-2",
            book_id: "book-1",
            index: 2,
            text: "窗边的夜色安静下来。",
            is_chapter_start: false,
          }],
        });
      }),
    });

    render(<PetShell night={false} onExpand={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: "Nyx 头像" }));
    fireEvent.click(screen.getByRole("button", { name: "读书" }));
    fireEvent.click(screen.getByRole("button", { name: /诺斯艾兰/ }));
    await waitFor(() => expect(screen.getByRole("region", { name: "陪伴读书" })).toBeInTheDocument());
    expect(screen.getByText("窗边的夜色安静下来。")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "返回上一级" }));
    expect(screen.getByRole("region", { name: "选择书目" })).toBeInTheDocument();
  });

  it("陪读二级界面可以前后翻页并在书籍边界禁用按钮", async () => {
    const syncPosition = vi.fn(async (next: number) => {
      useReaderStore.setState({ userPosition: next });
    });
    useReaderStore.setState({
      books: [{
        id: "book-1",
        title: "诺斯艾兰",
        author: "作者",
        filename: "book.epub",
        total_paragraphs: 3,
        user_position: 2,
        last_read_at: null,
      }],
      totalParagraphs: 3,
      userPosition: 2,
      openBook: vi.fn(async () => {
        useReaderStore.setState({
          bookId: "book-1",
          totalParagraphs: 3,
          userPosition: 2,
          paragraphs: [
            { id: "p-1", book_id: "book-1", index: 1, text: "第一页。", is_chapter_start: false },
            { id: "p-2", book_id: "book-1", index: 2, text: "第二页。", is_chapter_start: false },
            { id: "p-3", book_id: "book-1", index: 3, text: "第三页。", is_chapter_start: false },
          ],
        });
      }),
      syncPosition,
    });

    render(<PetShell night={false} onExpand={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: "Nyx 头像" }));
    fireEvent.click(screen.getByRole("button", { name: "读书" }));
    fireEvent.click(screen.getByRole("button", { name: /诺斯艾兰/ }));
    await waitFor(() => expect(screen.getByText("第二页。")).toBeInTheDocument());

    fireEvent.click(screen.getByRole("button", { name: "下一页" }));
    await waitFor(() => expect(screen.getByText("第三页。")).toBeInTheDocument());
    expect(syncPosition).toHaveBeenLastCalledWith(3);
    expect(screen.getByRole("button", { name: "下一页" })).toBeDisabled();

    fireEvent.click(screen.getByRole("button", { name: "上一页" }));
    await waitFor(() => expect(screen.getByText("第二页。")).toBeInTheDocument());
    expect(syncPosition).toHaveBeenLastCalledWith(2);
  });
});
