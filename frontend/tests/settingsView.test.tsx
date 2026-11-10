import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import * as api from "../src/api/client";
import SettingsView from "../src/components/layout/SettingsView";
import { useCharacterStore } from "../src/stores/characterStore";
import { useSettingsStore } from "../src/stores/settingsStore";

vi.mock("../src/api/client", async (importOriginal) => ({
  ...await importOriginal<typeof api>(),
  getCharacterCard: vi.fn(async (id: string) => ({
    id, name: id === "nyx" ? "Nyx" : "Alice", schema_version: 2,
    inner_document: [{ section: "identity", id: "self-understanding", title: "初始理解", content: `来自卡包：${id}` }],
    long_term_desires: [], assets: {}, canon: "# 身份与表达约定\n\n真实卡包内容", character_reference: "来自参考资料的内容",
  })),
}));

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

  it("移除圆圈和全部背景设置入口", () => {
    const { container } = render(<SettingsView onClose={() => {}} />);
    expect(screen.queryByText("圆圈背景")).not.toBeInTheDocument();
    expect(screen.queryByText("圆圈大小")).not.toBeInTheDocument();
    expect(container.querySelector('input[type="color"]')).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "上传图片" })).not.toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "背景" })).not.toBeInTheDocument();
  });

  it("点关闭按钮触发 onClose", () => {
    const onClose = vi.fn();
    render(<SettingsView onClose={onClose} />);
    fireEvent.click(screen.getByRole("button", { name: "关闭" }));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("外观与角色管理同列，右侧从接口读取当前角色的实际初始卡", async () => {
    const originalRegistry = useCharacterStore.getState().registry;
    act(() => useCharacterStore.setState({ registry: {
      current_id: "nyx", selection_revision: 1, limit: 4,
      characters: [{ id: "nyx", name: "Nyx", status: "RUNNING", error: null, sync_error: null, assets: {} }],
    } }));
    const { container } = render(<SettingsView onClose={() => {}} />);
    expect(container.querySelector(".settings-appearance")).toHaveTextContent("外观");
    expect(container.querySelector(".settings-sidebar > .character-manager")).toBeInTheDocument();
    expect(await screen.findByText("来自卡包：nyx")).toBeInTheDocument();
    expect(container.querySelector(".settings-layout > .settings-card")).toHaveTextContent("来自卡包：nyx");
    expect(api.getCharacterCard).toHaveBeenCalledWith("nyx", expect.any(AbortSignal));
    fireEvent.click(screen.getByRole("button", { name: "身份与表达约定" }));
    expect(screen.getByText("真实卡包内容")).toBeInTheDocument();
    expect(screen.queryByText("来自卡包：nyx")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "角色参考资料" }));
    expect(screen.getByText("来自参考资料的内容")).toBeInTheDocument();
    expect(screen.queryByText("真实卡包内容")).not.toBeInTheDocument();
    act(() => useCharacterStore.setState({ registry: {
      current_id: "alice", selection_revision: 2, limit: 4,
      characters: [{ id: "alice", name: "Alice", status: "RUNNING", error: null, sync_error: null, assets: {} }],
    } }));
    expect(await screen.findByText("来自卡包：alice")).toBeInTheDocument();
    expect(screen.queryByText("来自卡包：nyx")).not.toBeInTheDocument();
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

  it("新建角色进入独立表单，提交主题和按用途命名的图片", async () => {
    const originalCreate = useCharacterStore.getState().create;
    const create = vi.fn(async () => {});
    useCharacterStore.setState({ create });
    try {
      const { getByRole, getByPlaceholderText } = render(<SettingsView onClose={() => {}} />);
      fireEvent.click(getByRole("button", { name: "新建角色" }));
      expect(screen.queryByRole("region", { name: "外观" })).not.toBeInTheDocument();
      expect(screen.getByRole("heading", { name: "新建角色卡", level: 2 })).toBeInTheDocument();
      fireEvent.change(getByPlaceholderText("角色 ID"), { target: { value: "alice" } });
      fireEvent.change(getByPlaceholderText("角色名称"), { target: { value: "Alice" } });
      fireEvent.change(screen.getByLabelText("主题正文"), { target: { value: "一位新角色" } });
      const image = new File(["image"], "my-photo.png", { type: "image/png" });
      fireEvent.change(screen.getByLabelText("头像"), { target: { files: [image] } });
      fireEvent.click(getByRole("button", { name: "创建角色" }));
      expect(create).toHaveBeenCalledWith(expect.objectContaining({ id: "alice", name: "Alice", inner_document: [{ section: "identity", id: "self-understanding", title: "自我理解", content: "一位新角色" }] }), [expect.objectContaining({ name: "avatar.png" })]);
      await waitFor(() => expect(screen.getByRole("button", { name: "新建角色" })).toBeInTheDocument());
    } finally {
      act(() => useCharacterStore.setState({ create: originalCreate }));
    }
  });

  it("新建失败保留输入，提交期间阻止重复创建", async () => {
    const originalCreate = useCharacterStore.getState().create;
    let reject!: (error: Error) => void;
    const create = vi.fn(() => new Promise<void>((_, fail) => { reject = fail; }));
    useCharacterStore.setState({ create });
    try {
      render(<SettingsView />);
      fireEvent.click(screen.getByRole("button", { name: "新建角色" }));
      fireEvent.change(screen.getByLabelText("角色 ID"), { target: { value: "alice" } });
      fireEvent.change(screen.getByLabelText("角色名称"), { target: { value: "Alice" } });
      fireEvent.change(screen.getByLabelText("主题正文"), { target: { value: "保留这段设定" } });
      fireEvent.click(screen.getByRole("button", { name: "创建角色" }));
      expect(screen.getByRole("button", { name: "正在创建…" })).toBeDisabled();
      fireEvent.click(screen.getByRole("button", { name: "正在创建…" }));
      expect(create).toHaveBeenCalledTimes(1);
      await act(async () => reject(new Error("角色 ID 已存在")));
      expect(screen.getByRole("alert")).toHaveTextContent("角色 ID 已存在");
      expect(screen.getByLabelText("主题正文")).toHaveValue("保留这段设定");
    } finally {
      act(() => useCharacterStore.setState({ create: originalCreate }));
    }
  });
});
