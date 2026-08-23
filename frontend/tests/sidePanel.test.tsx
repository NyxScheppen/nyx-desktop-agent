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

  it("渲染 2 个标签，默认激活「背景」并显示其面板", () => {
    render(<SidePanel onBack={() => {}} />);
    for (const label of ["背景", "Eval"]) {
      expect(screen.getByRole("button", { name: label })).toBeInTheDocument();
    }
    expect(screen.getByRole("button", { name: "背景" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    // 默认显示背景面板；面板标题「背景」在 DOM
    expect(screen.getByRole("heading", { name: "背景" })).toBeInTheDocument();
  });

  it("点击「Eval」切换面板，未激活面板卸载", () => {
    // EvalPanel 挂载时 refresh() → getEval/getTokens → fetch；stub 永不 resolve，
    // 避免 fetch 微任务在 act 外触发 setState 的重渲染告警（本测只验证标签切换，不验数据）
    vi.stubGlobal("fetch", vi.fn(() => new Promise(() => {})));
    render(<SidePanel onBack={() => {}} />);
    fireEvent.click(screen.getByRole("button", { name: "Eval" }));
    expect(screen.getByRole("button", { name: "Eval" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    expect(screen.getByRole("heading", { name: "Eval" })).toBeInTheDocument();
    // 背景面板已卸载
    expect(screen.queryByRole("heading", { name: "背景" })).not.toBeInTheDocument();
  });

  it("「返回对话」按钮触发 onBack", () => {
    const onBack = vi.fn();
    render(<SidePanel onBack={onBack} />);
    fireEvent.click(screen.getByRole("button", { name: "返回对话" }));
    expect(onBack).toHaveBeenCalledTimes(1);
  });
});
