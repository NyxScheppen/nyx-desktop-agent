import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "../src/App";

vi.mock("@tauri-apps/api/event", () => ({
  listen: vi.fn().mockResolvedValue(vi.fn()),
}));

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
  default: ({ night }: { night: boolean }) => <div data-testid="avatar" data-night={night} />,
}));
vi.mock("../src/components/inner/InnerStatePanel", () => ({ default: () => null }));
vi.mock("../src/components/layout/SettingsView", () => ({ default: () => null }));
vi.mock("../src/components/panels/ActivityPanel", () => ({ default: () => null }));
vi.mock("../src/components/panels/DesiresPanel", () => ({ default: () => null }));
vi.mock("../src/components/panels/MemoryPanel", () => ({ default: () => null }));
vi.mock("../src/components/reading/BookshelfView", () => ({ default: () => null }));
vi.mock("../src/components/reading/ReaderView", () => ({ default: () => null }));
vi.mock("../src/components/shell/RightDock", () => ({ default: () => null }));
vi.mock("../src/components/shell/StatusBar", () => ({ default: () => null }));

describe("App 分钟时钟与昼夜同步", () => {
  beforeEach(() => vi.useFakeTimers());
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
