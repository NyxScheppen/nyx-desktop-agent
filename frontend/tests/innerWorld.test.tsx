import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import InnerWorld from "../src/components/layout/InnerWorld";
import { useInnerLifeStore } from "../src/stores/innerLifeStore";
import { useReadingNotesStore } from "../src/stores/readingNotesStore";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("InnerWorld 内心世界弹窗（单分类卡片）", () => {
  beforeEach(() => {
    // 默认「内在 → 内在状态」读 innerLifeStore.current；置 null 使其整体占位不渲染子组件
    useInnerLifeStore.setState({ current: null, error: null });
    useReadingNotesStore.setState({ notes: null, loading: false, error: null });
  });

  it("按 categoryIndex 渲染对应子标签，默认激活第一项", () => {
    render(<InnerWorld categoryIndex={0} onClose={() => {}} />);
    // 分类「内在」的子标签
    for (const label of ["内在状态", "欲望", "叙事"]) {
      expect(screen.getByRole("button", { name: label })).toBeInTheDocument();
    }
    expect(screen.getByRole("button", { name: "内在状态" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    expect(screen.getByRole("heading", { name: "内在状态" })).toBeInTheDocument();
  });

  it("categoryIndex=1 渲染「空间」子标签、默认激活「读书笔记」", () => {
    // ReadingNotesPanel 挂载时 refresh() → getReadingNotes → fetch；stub 永不 resolve，
    // 避免 fetch 微任务在 act 外触发 setState 的重渲染告警（本测只验证标签切换，不验数据）
    vi.stubGlobal("fetch", vi.fn(() => new Promise(() => {})));
    render(<InnerWorld categoryIndex={1} onClose={() => {}} />);
    for (const label of ["读书笔记", "产出", "资料"]) {
      expect(screen.getByRole("button", { name: label })).toBeInTheDocument();
    }
    expect(screen.getByRole("button", { name: "读书笔记" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    expect(screen.getByRole("heading", { name: "读书笔记" })).toBeInTheDocument();
  });

  it("分类内切子标签：点击「欲望」切到欲望面板", () => {
    vi.stubGlobal("fetch", vi.fn(() => new Promise(() => {})));
    render(<InnerWorld categoryIndex={0} onClose={() => {}} />);
    fireEvent.click(screen.getByRole("button", { name: "欲望" }));
    expect(screen.getByRole("button", { name: "欲望" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    expect(screen.getByRole("heading", { name: "欲望" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "内在状态" })).not.toBeInTheDocument();
  });

  it("「关闭」按钮触发 onClose", () => {
    const onClose = vi.fn();
    render(<InnerWorld categoryIndex={0} onClose={onClose} />);
    fireEvent.click(screen.getByRole("button", { name: "关闭" }));
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});
