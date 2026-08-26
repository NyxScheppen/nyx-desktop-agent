import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import InnerWorld from "../src/components/layout/InnerWorld";
import { useInnerLifeStore } from "../src/stores/innerLifeStore";
import { useReadingNotesStore } from "../src/stores/readingNotesStore";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("InnerWorld 内心世界页内面板（横向子标签切换）", () => {
  beforeEach(() => {
    // 活动面板挂载即 refresh() → fetch。stub 永不 resolve，
    // 避免 fetch 微任务在 act 外触发 setState 的重渲染告警（本测只验证 tab 切换，不验数据）
    vi.stubGlobal("fetch", vi.fn(() => new Promise(() => {})));
    useInnerLifeStore.setState({ current: null, error: null });
    useReadingNotesStore.setState({ notes: null, loading: false, error: null });
  });

  it("categoryIndex=0 渲染「内在」子标签，默认激活「内在状态」", () => {
    render(<InnerWorld categoryIndex={0} />);
    expect(screen.getByText("内在")).toBeInTheDocument();
    for (const label of ["内在状态", "欲望", "叙事"]) {
      expect(screen.getByRole("button", { name: label })).toBeInTheDocument();
    }
    expect(screen.getByRole("button", { name: "内在状态" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    expect(screen.getByRole("heading", { name: "内在状态" })).toBeInTheDocument();
  });

  it("categoryIndex=1 渲染「空间」子标签，默认激活「读书笔记」", () => {
    render(<InnerWorld categoryIndex={1} />);
    expect(screen.getByText("空间")).toBeInTheDocument();
    for (const label of ["读书笔记", "产出", "资料"]) {
      expect(screen.getByRole("button", { name: label })).toBeInTheDocument();
    }
    expect(screen.getByRole("button", { name: "读书笔记" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    expect(screen.getByRole("heading", { name: "读书笔记" })).toBeInTheDocument();
  });

  it("categoryIndex=2 渲染「记录」子标签，默认激活「活动」", () => {
    render(<InnerWorld categoryIndex={2} />);
    expect(screen.getByText("记录")).toBeInTheDocument();
    for (const label of ["活动", "记忆"]) {
      expect(screen.getByRole("button", { name: label })).toBeInTheDocument();
    }
    expect(screen.getByRole("button", { name: "活动" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    expect(screen.getByRole("heading", { name: "活动" })).toBeInTheDocument();
  });

  it("点子标签「欲望」切到欲望面板，内在状态面板卸载", () => {
    render(<InnerWorld categoryIndex={0} />);
    fireEvent.click(screen.getByRole("button", { name: "欲望" }));
    expect(screen.getByRole("button", { name: "欲望" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    expect(screen.getByRole("heading", { name: "欲望" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "内在状态" })).not.toBeInTheDocument();
  });
});
