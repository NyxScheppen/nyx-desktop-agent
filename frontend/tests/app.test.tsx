import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "../src/App";

const desktopWindowMocks = vi.hoisted(() => ({
  applyDesktopMode: vi.fn(async () => undefined),
  isTauriRuntime: vi.fn(() => false),
}));

vi.mock("../src/lib/desktopWindow", () => desktopWindowMocks);

vi.mock("../src/hooks/useSSE", () => ({ useSSE: () => "closed" }));
vi.mock("../src/hooks/usePresence", () => ({ usePresence: () => undefined }));
vi.mock("../src/stores/characterStore", () => {
  const state = {
    registry: { current_id: "nyx", selection_revision: 0, limit: 4,
      characters: [{ id: "nyx", name: "Nyx", status: "RUNNING", assets: {}, error: null }] },
    generation: 0, switching: false, error: null,
    initialize: vi.fn(), refresh: vi.fn(), select: vi.fn(),
  };
  return { useCharacterStore: Object.assign(
    (selector: (s: typeof state) => unknown) => selector(state),
    { getState: () => state },
  ) };
});
vi.mock("../src/stores/activityStore", () => ({
  useActivityStore: (selector: (state: { refresh: () => Promise<void> }) => unknown) =>
    selector({ refresh: vi.fn() }),
}));
vi.mock("../src/stores/chatStore", () => ({
  useChatStore: (selector: (state: { messages: never[]; loadHistory: () => Promise<void> }) => unknown) =>
    selector({ messages: [], loadHistory: vi.fn() }),
}));
vi.mock("../src/stores/innerLifeStore", () => ({
  useInnerLifeStore: (selector: (state: { refreshState: () => void }) => unknown) =>
    selector({ refreshState: vi.fn() }),
}));
vi.mock("../src/stores/readerStore", () => ({
  useReaderStore: (selector: (state: { bookId: null }) => unknown) => selector({ bookId: null }),
}));
vi.mock("../src/stores/settingsStore", () => ({
  useSettingsStore: (
    selector: (state: { tint: null; image: null; fontScale: "medium" }) => unknown,
  ) => selector({ tint: null, image: null, fontScale: "medium" }),
}));

vi.mock("../src/components/chat/ChatInput", () => ({ default: () => null }));
vi.mock("../src/components/chat/MessageList", () => ({ default: () => null }));
vi.mock("../src/components/inner/Avatar", () => ({
  default: ({ night, onDoubleClick }: { night: boolean; onDoubleClick?: () => void }) => (
    <button type="button" data-testid="avatar" data-night={night} onDoubleClick={onDoubleClick} />
  ),
}));
vi.mock("../src/components/desktop/PetShell", () => ({
  default: ({ onExpand, onOpenSettings }: { onExpand: () => void; onOpenSettings: () => void }) => (
    <><button type="button" aria-label="Nyx 桌宠头像" onDoubleClick={onExpand} />
      <button onClick={onOpenSettings}>桌宠设置</button></>
  ),
}));
vi.mock("../src/components/inner/InnerStatePanel", () => ({ default: () => null }));
vi.mock("../src/components/layout/SettingsView", () => ({
  default: ({ onClose }: { onClose?: () => void }) => onClose
    ? <div role="dialog" aria-label="设置"><button onClick={onClose}>关闭设置</button></div>
    : <section aria-label="设置" />,
}));
vi.mock("../src/components/panels/ActivityPanel", () => ({ default: () => null }));
vi.mock("../src/components/panels/CreationPanel", () => ({ default: () => null }));
vi.mock("../src/components/panels/DesiresPanel", () => ({ default: () => null }));
vi.mock("../src/components/panels/MemoryPanel", () => ({ default: () => null }));
vi.mock("../src/components/reading/BookshelfView", () => ({ default: () => null }));
vi.mock("../src/components/reading/ReaderView", () => ({ default: () => null }));
vi.mock("../src/components/shell/RightDock", () => ({
  default: ({ onReturnToPet, onOpenSettings, onSwitch }: {
    onReturnToPet?: () => void; onOpenSettings: () => void; onSwitch: (view: "chat") => void;
  }) => <>
    <button onClick={onOpenSettings}>设置</button>
    <button onClick={() => onSwitch("chat")}>聊天</button>
    {onReturnToPet && <button onClick={onReturnToPet}>收回桌宠</button>}
  </>,
}));
vi.mock("../src/components/AnnounceLayer", () => ({ default: () => <div data-testid="announcements" /> }));
vi.mock("../src/components/shell/StatusBar", () => ({ default: () => null }));

describe("App 分钟时钟与昼夜同步", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    desktopWindowMocks.isTauriRuntime.mockReturnValue(false);
  });
  afterEach(() => vi.useRealTimers());

  it.each([
    [new Date(2026, 8, 17, 5, 59, 59, 500), "day"],
    [new Date(2026, 8, 17, 21, 59, 59, 500), "night"],
  ])("跨分钟边界后自动切换到 %s", (start, phase) => {
    vi.setSystemTime(start);
    const { container } = render(<App />);

    act(() => vi.advanceTimersByTime(500));

    expect(container.firstElementChild).toHaveAttribute("data-time-phase", phase);
  });

  it.each(["focus", "visibilitychange"])("休眠后 %s 立即刷新并重新对齐分钟", (event) => {
    vi.setSystemTime(new Date(2026, 8, 17, 19, 0));
    const { container } = render(<App />);
    vi.setSystemTime(new Date(2026, 8, 18, 5, 59, 59, 500));
    if (event === "focus") fireEvent.focus(window);
    else fireEvent(document, new Event("visibilitychange"));
    expect(container.firstElementChild).toHaveAttribute("data-time-phase", "night");
    act(() => vi.advanceTimersByTime(500));
    expect(container.firstElementChild).toHaveAttribute("data-time-phase", "day");
  });
});

describe("App 桌宠与完整端切换", () => {
  beforeEach(() => {
    desktopWindowMocks.isTauriRuntime.mockReturnValue(true);
    desktopWindowMocks.applyDesktopMode.mockClear();
  });

  it("双击桌宠头像后重新挂载完整内容，避免只剩空白背景", async () => {
    const { container } = render(<App />);

    expect(container.querySelector(".app")).toHaveClass("app--pet");
    expect(screen.queryByText("✦ Nyx ✦")).not.toBeInTheDocument();

    fireEvent.doubleClick(screen.getByRole("button", { name: "Nyx 桌宠头像" }));

    expect(container.querySelector(".app")).toHaveClass("app--full");
    expect(screen.queryByTestId("avatar")).not.toBeInTheDocument();
    await waitFor(() => expect(desktopWindowMocks.applyDesktopMode).toHaveBeenCalledWith("full"));
    expect(screen.getByRole("main")).toBeInTheDocument();
  });

  it("完整端默认进入聊天，阅读按钮打开右侧抽屉", () => {
    desktopWindowMocks.isTauriRuntime.mockReturnValue(false);
    render(<App />);

    expect(screen.getByRole("main")).toHaveClass("game-shell--chat");
    const toggle = screen.getByRole("button", { name: "开始阅读" });
    fireEvent.click(toggle);

    expect(toggle).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByRole("main")).toHaveClass("game-shell--reader-open");
  });

  it("完整端从侧栏进入设置页面，离开阅读后仍可返回聊天", () => {
    desktopWindowMocks.isTauriRuntime.mockReturnValue(false);
    render(<App />);
    fireEvent.click(screen.getByRole("button", { name: "开始阅读" }));
    fireEvent.click(screen.getByRole("button", { name: "设置" }));
    expect(screen.getByRole("main")).toHaveClass("game-shell--page");
    expect(screen.getByRole("main")).not.toHaveClass("game-shell--reader-open");
    expect(screen.getByRole("region", { name: "设置" })).toBeInTheDocument();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "聊天" }));
    expect(screen.getByRole("main")).toHaveClass("game-shell--chat");
    expect(screen.queryByRole("region", { name: "设置" })).not.toBeInTheDocument();
  });

  it("桌宠小窗仍使用设置弹层，展开时关闭弹层", () => {
    const { container } = render(<App />);
    fireEvent.click(screen.getByRole("button", { name: "桌宠设置" }));
    expect(screen.getByRole("dialog", { name: "设置" })).toBeInTheDocument();
    expect(container.querySelector(".app")).toHaveClass("app--pet");
    fireEvent.doubleClick(screen.getByRole("button", { name: "Nyx 桌宠头像" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(container.querySelector(".app")).toHaveClass("app--full");
  });

  it("移除顶栏后仍能从完整端收回桌宠，并保留临时提示", async () => {
    const { container } = render(<App />);
    fireEvent.doubleClick(screen.getByRole("button", { name: "Nyx 桌宠头像" }));
    expect(screen.getByTestId("announcements")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "收回桌宠" }));
    expect(container.querySelector(".app")).toHaveClass("app--pet");
    expect(screen.queryByTestId("announcements")).not.toBeInTheDocument();
    await waitFor(() => expect(desktopWindowMocks.applyDesktopMode).toHaveBeenLastCalledWith("pet"));
  });
});
