import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import SidePanel from "../src/components/layout/SidePanel";
import { useInnerLifeStore } from "../src/stores/innerLifeStore";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("SidePanel 标签页", () => {
  beforeEach(() => {
    useInnerLifeStore.setState({ current: null, loading: false, error: null });
  });

  it("渲染 6 个标签，默认激活「内在」并显示其面板", () => {
    render(<SidePanel />);
    for (const label of ["内在", "欲望", "活动", "记忆", "Eval", "溯源"]) {
      expect(screen.getByRole("button", { name: label })).toBeInTheDocument();
    }
    expect(screen.getByRole("button", { name: "内在" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    // 默认显示内在面板（current=null → 占位）；面板标题「内在状态」在 DOM
    expect(screen.getByRole("heading", { name: "内在状态" })).toBeInTheDocument();
  });

  it("点击「欲望」切换面板，未激活面板卸载", () => {
    // DesiresPanel 挂载时 refresh() → getDesires → fetch；stub 永不 resolve，
    // 避免 fetch 微任务在 act 外触发 setState 的重渲染告警（本测只验证标签切换，不验数据）
    vi.stubGlobal("fetch", vi.fn(() => new Promise(() => {})));
    render(<SidePanel />);
    fireEvent.click(screen.getByRole("button", { name: "欲望" }));
    expect(screen.getByRole("button", { name: "欲望" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    expect(screen.getByRole("heading", { name: "欲望" })).toBeInTheDocument();
    // 内在面板已卸载
    expect(screen.queryByRole("heading", { name: "内在状态" })).not.toBeInTheDocument();
  });
});
