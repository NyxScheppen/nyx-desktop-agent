import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import RightDock from "../src/components/shell/RightDock";

describe("RightDock 右侧底部工具条", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("渲染聊天 / 内在 / 空间 / 记录 / 出门 / 游戏设置六个入口", () => {
    render(<RightDock view={null} onSwitch={() => {}} />);
    for (const label of ["聊天", "内在", "空间", "记录", "出门", "游戏设置"]) {
      expect(screen.getByRole("button", { name: label })).toBeInTheDocument();
    }
  });

  it("当前视图对应入口高亮（aria-pressed）", () => {
    const { rerender } = render(<RightDock view={null} onSwitch={() => {}} />);
    expect(screen.getByRole("button", { name: "聊天" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    rerender(<RightDock view={0} onSwitch={() => {}} />);
    expect(screen.getByRole("button", { name: "内在" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    expect(screen.getByRole("button", { name: "聊天" })).toHaveAttribute(
      "aria-pressed",
      "false",
    );
    rerender(<RightDock view={1} onSwitch={() => {}} />);
    expect(screen.getByRole("button", { name: "空间" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    rerender(<RightDock view={2} onSwitch={() => {}} />);
    expect(screen.getByRole("button", { name: "记录" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    rerender(<RightDock view="explore" onSwitch={() => {}} />);
    expect(screen.getByRole("button", { name: "出门" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    rerender(<RightDock view="settings" onSwitch={() => {}} />);
    expect(screen.getByRole("button", { name: "游戏设置" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
  });

  it("点入口触发 onSwitch 对应视图", () => {
    const onSwitch = vi.fn();
    render(<RightDock view={null} onSwitch={onSwitch} />);
    fireEvent.click(screen.getByRole("button", { name: "聊天" }));
    fireEvent.click(screen.getByRole("button", { name: "内在" }));
    fireEvent.click(screen.getByRole("button", { name: "空间" }));
    fireEvent.click(screen.getByRole("button", { name: "记录" }));
    fireEvent.click(screen.getByRole("button", { name: "出门" }));
    fireEvent.click(screen.getByRole("button", { name: "游戏设置" }));
    expect(onSwitch).toHaveBeenNthCalledWith(1, null);
    expect(onSwitch).toHaveBeenNthCalledWith(2, 0);
    expect(onSwitch).toHaveBeenNthCalledWith(3, 1);
    expect(onSwitch).toHaveBeenNthCalledWith(4, 2);
    expect(onSwitch).toHaveBeenNthCalledWith(5, "explore");
    expect(onSwitch).toHaveBeenNthCalledWith(6, "settings");
  });
});
