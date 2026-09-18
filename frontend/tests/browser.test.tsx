import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { invoke, isTauri } from "@tauri-apps/api/core";
import { getCurrentWindow } from "@tauri-apps/api/window";
import { LogicalSize } from "@tauri-apps/api/dpi";
import BrowserView from "../src/components/browsing/BrowserView";
import { useBrowserStore } from "../src/stores/browserStore";
import { useChatStore } from "../src/stores/chatStore";
import ChatInput from "../src/components/chat/ChatInput";
import MessageBubble from "../src/components/chat/MessageBubble";

vi.mock("@tauri-apps/api/core", () => ({ invoke: vi.fn(), isTauri: vi.fn() }));
vi.mock("@tauri-apps/api/event", () => ({ listen: vi.fn().mockResolvedValue(vi.fn()) }));
vi.mock("@tauri-apps/api/window", () => ({ getCurrentWindow: vi.fn() }));

const result = {
  ok: true, session_id: "s", navigation_id: "n1", page_id: "p1", revision: 1,
  current_url: "https://example.com/", title: "Example", loading: false,
  can_go_back: false, can_go_forward: false, capture_paused: false, integration_status: "open",
};

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(isTauri).mockReturnValue(false);
  useBrowserStore.setState(useBrowserStore.getInitialState(), true);
  useBrowserStore.getState().acceptResult(result);
});
afterEach(() => vi.unstubAllGlobals());

describe("browser host state", () => {
  it("popup permission uses the normal command guard and controlled errors", async () => {
    vi.mocked(invoke).mockResolvedValueOnce(result).mockRejectedValueOnce({ code: "popup_not_allowed" });
    expect(await useBrowserStore.getState().allowPopup()).toBe(true);
    expect(await useBrowserStore.getState().allowPopup()).toBe(false);
    expect(useBrowserStore.getState().error).toContain("弹窗");
  });

  it("popup closure stays paused without reporting login success", () => {
    useBrowserStore.getState().acceptEvent({ ...result, kind: "popup_result", capture_paused: true, error_code: "cancelled" });
    expect(useBrowserStore.getState().pageId).toBeNull();
    expect(useBrowserStore.getState().error).toBe("登录弹窗已关闭");
  });
  it("new navigation immediately drops page context; late capture cannot restore it", async () => {
    let resolve!: (value: typeof result) => void;
    vi.mocked(invoke).mockImplementation(() => new Promise((done) => { resolve = done; }));
    const pending = useBrowserStore.getState().capture();
    useBrowserStore.getState().acceptEvent({ ...result, kind: "navigation_started", navigation_id: "n2", loading: true, capture_paused: true });
    resolve(result);
    await pending;
    expect(useBrowserStore.getState().navigationId).toBe("n2");
    expect(useBrowserStore.getState().pageId).toBeNull();
  });

  it("focus response loss preserves the id; a second explicit action gets a new id", async () => {
    vi.mocked(invoke).mockRejectedValueOnce({ code: "backend_unavailable", retryable: true })
      .mockResolvedValue(result);
    await useBrowserStore.getState().focus();
    await useBrowserStore.getState().focus();
    await useBrowserStore.getState().focus();
    const ids = vi.mocked(invoke).mock.calls.map(([, args]) => (args as Record<string, unknown>)?.focusId);
    expect(ids[0]).toBe(ids[1]);
    expect(ids[2]).not.toBe(ids[1]);
  });

  it("stale non-navigation events never change the current page", () => {
    useBrowserStore.getState().acceptEvent({ ...result, kind: "load_failed", navigation_id: "old", capture_paused: true });
    expect(useBrowserStore.getState().pageId).toBe("p1");
    expect(useBrowserStore.getState().capturePaused).toBe(false);
  });

  it("privacy and crash events clear page context", () => {
    useBrowserStore.getState().acceptEvent({ ...result, kind: "auth_state_changed", capture_paused: true });
    expect(useBrowserStore.getState().pageId).toBeNull();
    expect(useBrowserStore.getState().capturePaused).toBe(true);
  });

  it("an invalid bridge token pauses capture and drops page context", async () => {
    vi.mocked(invoke).mockRejectedValue({ code: "invalid_bridge_token" });
    await useBrowserStore.getState().focus();
    expect(useBrowserStore.getState().pageId).toBeNull();
    expect(useBrowserStore.getState().capturePaused).toBe(true);
    expect(useBrowserStore.getState().pendingFocusId).toBeNull();
  });

  it("engine navigation invalidation clears context before backend acknowledgement", () => {
    useBrowserStore.getState().acceptEvent({ ...result, kind: "auth_state_changed", navigation_id: "n2", loading: true, capture_paused: true });
    expect(useBrowserStore.getState().navigationId).toBe("n2");
    expect(useBrowserStore.getState().pageId).toBeNull();
  });

  it("hiding marks the view inactive before waiting for native confirmation", async () => {
    vi.mocked(invoke).mockResolvedValue(result);
    const hidden = useBrowserStore.getState().setVisible(false);
    expect(useBrowserStore.getState().visible).toBe(false);
    await hidden;
    expect(invoke).toHaveBeenCalledWith("browser_hide");
  });
});

it("ordinary browser development never embeds or invokes a native browser", async () => {
  await act(async () => { render(<BrowserView active />); });
  expect(screen.getByText("共同浏览不可用")).toBeInTheDocument();
  expect(invoke).not.toHaveBeenCalled();
});

it("history loads on demand without installing a polling timer", async () => {
  const fetchMock = vi.fn().mockResolvedValue({
    ok: true,
    status: 200,
    json: async () => ({ session: { id: "s" }, pages: [], next_cursor: null }),
  });
  vi.stubGlobal("fetch", fetchMock);
  const interval = vi.spyOn(globalThis, "setInterval");
  vi.mocked(invoke).mockResolvedValue(undefined);

  render(<BrowserView active />);
  await act(async () => { fireEvent.click(screen.getByRole("button", { name: "浏览记录" })); });

  expect(fetchMock).toHaveBeenCalledTimes(1);
  expect(interval).not.toHaveBeenCalled();
  interval.mockRestore();
});

it("history appends cursor pages and prevents overlapping requests", async () => {
  let finish!: (response: unknown) => void;
  const fetchMock = vi.fn().mockResolvedValueOnce({ ok: true, status: 200,
    json: async () => ({ session: { id: "s" }, pages: [{ id: "p1", title: "First", url: "https://example.com/1", status: "open" }], next_cursor: "p1" }),
  }).mockImplementationOnce(() => new Promise((resolve) => { finish = resolve; }));
  vi.stubGlobal("fetch", fetchMock);
  vi.mocked(invoke).mockResolvedValue(undefined);
  render(<BrowserView active />);
  await act(async () => { fireEvent.click(screen.getByRole("button", { name: "浏览记录" })); });
  fireEvent.click(screen.getByRole("button", { name: "加载更多" }));
  fireEvent.click(screen.getByRole("button", { name: "加载更多" }));

  expect(fetchMock).toHaveBeenCalledTimes(2);
  expect(fetchMock.mock.calls[1][0]).toBe("/api/browsing/sessions/s?limit=50&cursor=p1");
  await act(async () => { finish({ ok: true, status: 200,
    json: async () => ({ session: { id: "s" }, pages: [{ id: "p2", title: "Second", url: "https://example.com/2", status: "failed" }], next_cursor: null }),
  }); });
  expect(screen.getByText("First")).toBeInTheDocument();
  expect(screen.getByText("Second")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "加载更多" })).not.toBeInTheDocument();
});

it("deleting all history clears rows and cursor without reading the deleted session", async () => {
  const fetchMock = vi.fn().mockResolvedValueOnce({ ok: true, status: 200,
    json: async () => ({ session: { id: "s" }, pages: [{ id: "p1", title: "First", url: "https://example.com/1", status: "remembered" }], next_cursor: "p1" }),
  }).mockResolvedValue({ ok: true, status: 204 });
  vi.stubGlobal("fetch", fetchMock);
  vi.spyOn(window, "confirm").mockReturnValue(true);
  vi.mocked(invoke).mockResolvedValue(undefined);
  useBrowserStore.setState({ closed: true, pageId: null });
  render(<BrowserView active />);
  await act(async () => { fireEvent.click(screen.getByRole("button", { name: "浏览记录" })); });
  await act(async () => { fireEvent.click(screen.getByRole("button", { name: "删除浏览 checkpoint 与 Nyx 浏览记忆" })); });

  expect(fetchMock).toHaveBeenCalledTimes(2);
  expect(screen.queryByText("First")).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "加载更多" })).not.toBeInTheDocument();
  expect(screen.getByText("暂无浏览记录")).toBeInTheDocument();
  vi.mocked(window.confirm).mockRestore();
});

it("activating browsing expands a narrow native window before creating a child", async () => {
  vi.mocked(isTauri).mockReturnValue(true);
  const setSize = vi.fn().mockResolvedValue(undefined);
  vi.mocked(getCurrentWindow).mockReturnValue({
    innerSize: vi.fn().mockResolvedValue({ width: 800, height: 1400 }),
    scaleFactor: vi.fn().mockResolvedValue(2), setSize,
  } as unknown as ReturnType<typeof getCurrentWindow>);
  vi.stubGlobal("ResizeObserver", class { observe(): void {} disconnect(): void {} });
  useBrowserStore.setState(useBrowserStore.getInitialState(), true);
  await act(async () => { render(<BrowserView active />); });
  expect(setSize).toHaveBeenCalledWith(new LogicalSize(960, 700));
  expect(invoke).not.toHaveBeenCalled();
});

it("browsing question reply uses its attempt id and the active page context", async () => {
  useChatStore.getState().reset();
  const send = vi.spyOn(useChatStore.getState(), "sendMessage").mockResolvedValue(true);
  render(<><MessageBubble message={{ id: "question", kind: "browsing_question", role: "nyx", content: "怎么看？", correlation_id: "p1", timestamp: 1, attemptId: "attempt" }} ready onTyped={() => undefined} /><ChatInput browsingPageId="p1" /></>);
  fireEvent.click(screen.getByRole("button", { name: "回复此提问" }));
  fireEvent.change(screen.getByPlaceholderText("对 Nyx 说…"), { target: { value: "我的理解" } });
  await act(async () => { fireEvent.click(screen.getByRole("button", { name: "发送" })); });
  expect(send).toHaveBeenCalledWith("我的理解", { browsing_page_id: "p1", reply_to: "attempt" });
  send.mockRestore();
});

it("a completed send does not clear a newer reply selection", async () => {
  useChatStore.getState().reset();
  useChatStore.getState().setReplyTo("first");
  let finish!: (accepted: boolean) => void;
  const send = vi.spyOn(useChatStore.getState(), "sendMessage").mockImplementation(() => new Promise((resolve) => { finish = resolve; }));
  render(<ChatInput />);
  fireEvent.change(screen.getByPlaceholderText("对 Nyx 说…"), { target: { value: "回答" } });
  fireEvent.click(screen.getByRole("button", { name: "发送" }));
  act(() => { useChatStore.getState().setReplyTo("next"); });
  await act(async () => { finish(true); });
  expect(useChatStore.getState().replyTo).toBe("next");
  send.mockRestore();
});
