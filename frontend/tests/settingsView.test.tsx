import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import SettingsView from "../src/components/layout/SettingsView";
import { useEvalStore } from "../src/stores/evalStore";
import { useSettingsStore } from "../src/stores/settingsStore";

describe("SettingsView 设置弹层", () => {
  beforeEach(() => {
    useSettingsStore.getState().reset();
    useEvalStore.setState({ records: null, stats: null, error: null });
    // 阻断 EvalPanel mount 时的 refresh() 真实 fetch（本测只验设置项渲染）
    vi.spyOn(useEvalStore.getState(), "refresh").mockResolvedValue(undefined);
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("渲染标题 / 字体大小三档 / 圆圈背景 / 圆圈大小 / 背景外观", () => {
    render(<SettingsView onClose={() => {}} />);
    expect(screen.getByRole("dialog", { name: "设置" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "字体大小" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "圆圈背景" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "圆圈大小" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "背景" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "LLM 调用 / token" })).toBeInTheDocument();
    for (const label of ["小", "中", "大"]) {
      expect(screen.getByRole("button", { name: label })).toBeInTheDocument();
    }
  });

  it("默认「中」激活，点「大」写 settingsStore.fontScale", () => {
    render(<SettingsView onClose={() => {}} />);
    expect(screen.getByRole("button", { name: "中" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    fireEvent.click(screen.getByRole("button", { name: "大" }));
    expect(useSettingsStore.getState().fontScale).toBe("large");
    expect(screen.getByRole("button", { name: "大" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
  });

  it("点预设色块「樱粉」写 settingsStore.tint", () => {
    render(<SettingsView onClose={() => {}} />);
    fireEvent.click(screen.getByRole("button", { name: "樱粉" }));
    expect(useSettingsStore.getState().tint).toBe("#f7e8e0");
  });

  it("点圆圈底色「浅粉」写 settingsStore.circleColor", () => {
    render(<SettingsView onClose={() => {}} />);
    fireEvent.click(screen.getByRole("button", { name: "浅粉" }));
    expect(useSettingsStore.getState().circleColor).toBe("#f7e8e0");
  });

  it("默认「大」激活，点圆圈尺寸「小」写 settingsStore.circleSize", () => {
    render(<SettingsView onClose={() => {}} />);
    expect(screen.getByRole("button", { name: "圆圈大" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    fireEvent.click(screen.getByRole("button", { name: "圆圈小" }));
    expect(useSettingsStore.getState().circleSize).toBe("small");
    expect(screen.getByRole("button", { name: "圆圈小" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
  });

  it("点关闭按钮触发 onClose", () => {
    const onClose = vi.fn();
    render(<SettingsView onClose={onClose} />);
    fireEvent.click(screen.getByRole("button", { name: "关闭" }));
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});
