import { act, fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";
import SettingsView from "../src/components/layout/SettingsView";
import { useCharacterStore } from "../src/stores/characterStore";
import { useSettingsStore } from "../src/stores/settingsStore";

describe("SettingsView 设置弹层", () => {
  beforeEach(() => {
    useSettingsStore.getState().reset();
  });

  it("完整页面复用外观与角色管理，不创建设置对话框", () => {
    render(<SettingsView />);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(screen.getByRole("region", { name: "设置" })).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "外观" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "编年史" })).toBeInTheDocument();
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

  it("窗口风格只在设置中切换并写入 settingsStore.layoutStyle", () => {
    render(<SettingsView onClose={() => {}} />);
    expect(screen.getByRole("button", { name: "双开本" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    fireEvent.click(screen.getByRole("button", { name: "编年史" }));
    expect(useSettingsStore.getState().layoutStyle).toBe("chronicle");
    expect(screen.getByRole("button", { name: "编年史" })).toHaveAttribute(
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

  it("外观与角色管理分列，并展示内置 Nyx 的真实 canon", () => {
    const originalRegistry = useCharacterStore.getState().registry;
    act(() => useCharacterStore.setState({ registry: {
      current_id: "nyx", selection_revision: 1, limit: 4,
      characters: [{ id: "nyx", name: "Nyx", status: "RUNNING", error: null, sync_error: null, assets: {} }],
    } }));
    const { container } = render(<SettingsView onClose={() => {}} />);
    expect(container.querySelector(".settings-appearance")).toHaveTextContent("外观");
    expect(container.querySelector(".settings-layout > .character-manager")).toBeInTheDocument();
    expect(screen.getByText(/以小说《诺斯艾兰》的尼克斯·夏本为原型/)).toBeInTheDocument();
    act(() => useCharacterStore.setState({ registry: originalRegistry }));
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

  it("新建角色表单提交文字设定和图片", () => {
    const originalCreate = useCharacterStore.getState().create;
    const create = vi.fn(async () => {});
    useCharacterStore.setState({ create });
    try {
      const { getByRole, getByPlaceholderText, container } = render(<SettingsView onClose={() => {}} />);
      fireEvent.click(getByRole("button", { name: "新建角色" }));
      fireEvent.change(getByPlaceholderText("角色 ID"), { target: { value: "alice" } });
      fireEvent.change(getByPlaceholderText("角色名称"), { target: { value: "Alice" } });
      fireEvent.change(getByPlaceholderText("身份设定"), { target: { value: "一位新角色" } });
      const image = new File(["image"], "avatar.png", { type: "image/png" });
      fireEvent.change(container.querySelector<HTMLInputElement>('.character-manager__form input[type="file"]')!, { target: { files: [image] } });
      fireEvent.click(getByRole("button", { name: "创建角色" }));
      expect(create).toHaveBeenCalledWith(expect.objectContaining({ id: "alice", name: "Alice", identity: "一位新角色" }), [image]);
    } finally {
      act(() => useCharacterStore.setState({ create: originalCreate }));
    }
  });
});
