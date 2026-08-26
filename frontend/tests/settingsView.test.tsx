import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";
import SettingsView from "../src/components/layout/SettingsView";
import { useSettingsStore } from "../src/stores/settingsStore";

describe("SettingsView 游戏设置页内面板", () => {
  beforeEach(() => {
    useSettingsStore.getState().reset();
  });

  it("渲染标题 / 字体大小三档 / 背景外观", () => {
    render(<SettingsView />);
    expect(screen.getByText("游戏设置")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "字体大小" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "背景" })).toBeInTheDocument();
    for (const label of ["小", "中", "大"]) {
      expect(screen.getByRole("button", { name: label })).toBeInTheDocument();
    }
  });

  it("默认「中」激活，点「大」写 settingsStore.fontScale", () => {
    render(<SettingsView />);
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
    render(<SettingsView />);
    fireEvent.click(screen.getByRole("button", { name: "樱粉" }));
    expect(useSettingsStore.getState().tint).toBe("#f7e8e0");
  });
});
