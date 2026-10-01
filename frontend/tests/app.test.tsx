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
  default: ({ onExpand }: { onExpand: () => void }) => (
    <button type="button" aria-label="Nyx 桌宠头像" onDoubleClick={onExpand} />
  ),
}));
vi.mock("../src/components/inner/InnerStatePanel", () => ({ default: () => null }));
vi.mock("../src/components/layout/SettingsView", () => ({ default: () => null }));
vi.mock("../src/components/panels/ActivityPanel", () => ({ default: () => null }));
vi.mock("../src/components/panels/CreationPanel", () => ({ default: () => null }));
vi.mock("../src/components/panels/DesiresPanel", () => ({ default: () => null }));
vi.mock("../src/components/panels/MemoryPanel", () => ({ default: () => null }));
vi.mock("../src/components/reading/BookshelfView", () => ({ default: () => null }));
vi.mock("../src/components/reading/ReaderView", () => ({ default: () => null }));
vi.mock("../src/components/shell/RightDock", () => ({ default: () => null }));
vi.mock("../src/components/shell/StatusBar", () => ({ default: () => null }));

describe("App 分钟时钟与昼夜同步", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    desktopWindowMocks.isTauriRuntime.mockReturnValue(false);
  });
  afterEach(() => vi.useRealTimers());

  it.each([
    [new Date(2026, 8, 17, 5, 59, 59, 500), "day", "9月17日 星期四 06:00", "false"],
    [new Date(2026, 8, 17, 21, 59, 59, 500), "night", "9月17日 星期四 22:00", "true"],
  ])("跨分钟边界后自动切换到 %s", (start, phase, label, avatarNight) => {
    vi.setSystemTime(start);
    const { container } = render(<App />);

    act(() => vi.advanceTimersByTime(500));

    expect(container.firstElementChild).toHaveAttribute("data-time-phase", phase);
    expect(screen.getByText(label)).toBeInTheDocument();
    expect(screen.getByTestId("avatar")).toHaveAttribute("data-night", avatarNight);
  });

  it.each(["focus", "visibilitychange"])("休眠后 %s 立即刷新并重新对齐分钟", (event) => {
    vi.setSystemTime(new Date(2026, 8, 17, 19, 0));
    const { container } = render(<App />);
    vi.setSystemTime(new Date(2026, 8, 18, 5, 59, 59, 500));
    if (event === "focus") fireEvent.focus(window);
    else fireEvent(document, new Event("visibilitychange"));
    expect(container.firstElementChild).toHaveAttribute("data-time-phase", "night");
    expect(screen.getByText("9月18日 星期五 05:59")).toBeInTheDocument();
    act(() => vi.advanceTimersByTime(500));
    expect(screen.getByText("9月18日 星期五 06:00")).toBeInTheDocument();
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
    expect(screen.getByText("✦ Nyx ✦")).toBeInTheDocument();
    expect(screen.getByTestId("avatar")).toBeInTheDocument();
    await waitFor(() => expect(desktopWindowMocks.applyDesktopMode).toHaveBeenCalledWith("full"));
  });
});
