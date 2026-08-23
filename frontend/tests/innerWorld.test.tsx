import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import InnerWorld from "../src/components/layout/InnerWorld";
import { useInnerLifeStore } from "../src/stores/innerLifeStore";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("InnerWorld 内心世界抽屉", () => {
  beforeEach(() => {
    // 默认激活「内在」面板读 innerLifeStore.current；置 null 使其整体占位不渲染子组件
    useInnerLifeStore.setState({ current: null, loading: false, error: null });
  });

  it("渲染 7 个标签，默认激活「内在」", () => {
    render(<InnerWorld open onClose={() => {}} />);
    for (const label of ["内在", "欲望", "活动", "产出", "叙事", "资料", "记忆"]) {
      expect(screen.getByRole("button", { name: label })).toBeInTheDocument();
    }
    expect(screen.getByRole("button", { name: "内在" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    // 默认显示内在面板；面板标题「内在状态」在 DOM
    expect(screen.getByRole("heading", { name: "内在状态" })).toBeInTheDocument();
  });

  it("点击「欲望」切换面板，未激活面板卸载", () => {
    // DesiresPanel 挂载时 refresh() → getDesires → fetch；stub 永不 resolve，
    // 避免 fetch 微任务在 act 外触发 setState 的重渲染告警（本测只验证标签切换，不验数据）
    vi.stubGlobal("fetch", vi.fn(() => new Promise(() => {})));
    render(<InnerWorld open onClose={() => {}} />);
    fireEvent.click(screen.getByRole("button", { name: "欲望" }));
    expect(screen.getByRole("button", { name: "欲望" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    expect(screen.getByRole("heading", { name: "欲望" })).toBeInTheDocument();
    // 内在面板已卸载
    expect(screen.queryByRole("heading", { name: "内在状态" })).not.toBeInTheDocument();
  });

  it("「收起」按钮触发 onClose", () => {
    const onClose = vi.fn();
    render(<InnerWorld open onClose={onClose} />);
    fireEvent.click(screen.getByRole("button", { name: "收起" }));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("open=false 时容器不带 inner-world--open 修饰类", () => {
    render(<InnerWorld open={false} onClose={() => {}} />);
    // 收起态：容器无 open 修饰类（CSS transform 滑出屏幕）
    expect(document.querySelector(".inner-world")).not.toHaveClass("inner-world--open");
  });
});
