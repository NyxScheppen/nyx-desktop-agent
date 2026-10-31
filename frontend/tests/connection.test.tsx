import { act, cleanup, fireEvent, render, renderHook, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import App from "../src/App";
import { getState } from "../src/api/client";
import { useSSE } from "../src/hooks/useSSE";
import { useConnectionStore } from "../src/stores/connectionStore";
import { useCharacterStore } from "../src/stores/characterStore";

const streams: ReadableStreamDefaultController<Uint8Array>[] = [];
const frame = (id: string, content = "你好🙂") =>
  `id: nyx:${id}\r\nevent: speak\r\ndata: ${JSON.stringify({
    event_id: id, correlation_id: "c", timestamp: 1, content,
  })}\r\n\r\n`;

beforeEach(() => {
  streams.length = 0;
  useConnectionStore.setState({ token: "test-token", required: false });
  useCharacterStore.setState({ switching: false, error: null });
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
  useConnectionStore.setState({ token: "", required: false });
  sessionStorage.clear();
});

function serveStreams() {
  const fetch = vi.fn(async (_url: RequestInfo | URL, _init?: RequestInit) => new Response(new ReadableStream<Uint8Array>({
    start(controller) { streams.push(controller); },
  }), { headers: { "Content-Type": "text/event-stream" } }));
  vi.stubGlobal("fetch", fetch);
  return fetch;
}

it("streaming fetch preserves UTF-8, CRLF and multiple frames with Bearer", async () => {
  const fetch = serveStreams();
  const dispatch = vi.fn();
  renderHook(() => useSSE(dispatch));
  await act(async () => {
    const bytes = new TextEncoder().encode(": heartbeat\r\n\r\n" + frame("a") + frame("b"));
    for (const byte of bytes) streams[0].enqueue(new Uint8Array([byte]));
  });
  expect(dispatch.mock.calls.map(([event]) => event.event_id)).toEqual(["a", "b"]);
  expect(dispatch.mock.calls[0][0].content).toBe("你好🙂");
  expect(new Headers(fetch.mock.calls[0][1]?.headers).get("Authorization")).toBe("Bearer test-token");
});

it("each reader reconnects from its own last complete frame and aborts on unmount", async () => {
  vi.useFakeTimers();
  const fetch = serveStreams();
  const first = vi.fn();
  const second = vi.fn();
  const a = renderHook(() => useSSE(first));
  const b = renderHook(() => useSSE(second));
  await act(async () => {
    streams[0].enqueue(new TextEncoder().encode(frame("a") + "id: nyx:unfinished\n"));
    streams[1].enqueue(new TextEncoder().encode(frame("other")));
    streams[0].close();
  });
  await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
  expect(new Headers(fetch.mock.calls[2][1]?.headers).get("Last-Event-ID")).toBe("nyx:a");
  await act(async () => { streams[2].enqueue(new TextEncoder().encode(frame("a") + frame("b"))); });
  expect(first.mock.calls.map(([event]) => event.event_id)).toEqual(["a", "b"]);
  expect(second.mock.calls.map(([event]) => event.event_id)).toEqual(["other"]);
  a.unmount(); b.unmount();
  expect((fetch.mock.calls[2][1]?.signal as AbortSignal).aborted).toBe(true);
});

it("401 stops reconnect and asks for credentials", async () => {
  const fetch = vi.fn(async () => new Response("", { status: 401 }));
  vi.stubGlobal("fetch", fetch);
  const dispatch = vi.fn();
  renderHook(() => useSSE(dispatch));
  await waitFor(() => expect(useConnectionStore.getState().required).toBe(true));
  expect(fetch).toHaveBeenCalledTimes(1);
});

it("REST adds Bearer and rejects a missing or expired credential", async () => {
  const fetch = vi.fn(async (_url: RequestInfo | URL, _init?: RequestInit) => new Response('{"detail":"Authentication required"}', { status: 401 }));
  vi.stubGlobal("fetch", fetch);
  await expect(getState()).rejects.toThrow("Authentication required");
  expect(new Headers(fetch.mock.calls[0][1]?.headers).get("Authorization")).toBe("Bearer test-token");
  expect(useConnectionStore.getState().required).toBe(true);
});

it("credential form saves only for the session and retries the app", async () => {
  useConnectionStore.setState({ token: "", required: true });
  vi.stubGlobal("fetch", vi.fn(async () => new Response("{}")));
  const view = render(<App />);
  fireEvent.change(screen.getByLabelText("访问凭证"), { target: { value: "new-token" } });
  fireEvent.click(screen.getByRole("button", { name: "连接" }));
  expect(sessionStorage.getItem("nyx.authToken")).toBe("new-token");
  expect(screen.queryByLabelText("访问凭证")).not.toBeInTheDocument();
  view.unmount();
  act(() => useConnectionStore.getState().clearToken());
  expect(sessionStorage.getItem("nyx.authToken")).toBeNull();
});

it("redeems a pairing code and stores only the returned device token in the session", async () => {
  useConnectionStore.setState({ token: "", required: true });
  const fetch = vi.fn(async () => new Response(JSON.stringify({
    device_id: "phone", access_token: "device-token",
  })));
  vi.stubGlobal("fetch", fetch);
  render(<App />);
  fireEvent.click(screen.getByRole("button", { name: "使用配对码" }));
  fireEvent.change(screen.getByLabelText("配对码"), { target: { value: "123-code" } });
  fireEvent.click(screen.getByRole("button", { name: "连接" }));
  await waitFor(() => expect(sessionStorage.getItem("nyx.authToken")).toBe("device-token"));
  const init = (fetch.mock.calls[0] as unknown as [RequestInfo, RequestInit])[1];
  expect(JSON.parse(init.body as string)).toEqual({
    code: "123-code", name: "手机",
  });
});
