import { act, fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";
import SettingsView from "../src/components/layout/SettingsView";
import { useCharacterStore } from "../src/stores/characterStore";
import { useSettingsStore } from "../src/stores/settingsStore";

describe("SettingsView 设置弹层", () => {
  beforeEach(() => {
    useSettingsStore.getState().reset();
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

  it("角色卡导入按钮打开文件选择并提交选中的卡包", () => {
    const originalImport = useCharacterStore.getState().import;
    const importCard = vi.fn(async () => {});
    useCharacterStore.setState({ import: importCard });
    try {
      const { container } = render(<SettingsView onClose={() => {}} />);
      const input = container.querySelector<HTMLInputElement>('input[type="file"][accept=".nyxcard,.zip"]');
      expect(input).not.toBeNull();
      expect(input).toHaveAttribute("hidden");
      const open = vi.spyOn(input!, "click").mockImplementation(() => {});
      fireEvent.click(screen.getByRole("button", { name: "导入角色卡" }));
      expect(open).toHaveBeenCalledOnce();
      const file = new File(["card"], "alice.nyxcard", { type: "application/zip" });
      fireEvent.change(input!, { target: { files: [file] } });
      expect(importCard).toHaveBeenCalledWith(file);
    } finally {
      act(() => useCharacterStore.setState({ import: originalImport }));
    }
  });
});
