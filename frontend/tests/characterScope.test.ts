import { afterEach, expect, it, vi } from "vitest";
import { bindScope, captureScope, invalidateScope, isScopeCurrent } from "../src/api/characterScope";
import { deleteBookmark, postChat } from "../src/api/client";
import { useChatStore } from "../src/stores/chatStore";
import { useReaderStore } from "../src/stores/readerStore";

afterEach(() => { bindScope(null); vi.unstubAllGlobals(); });

it("invalidates old callbacks even after A to B to A", () => {
  bindScope({ id: "alice", revision: 1 });
  const old = captureScope();
  invalidateScope();
  bindScope({ id: "bob", revision: 2 });
  bindScope({ id: "alice", revision: 3 });
  expect(isScopeCurrent(old)).toBe(false);
});

it("ignores a late chat failure after A to B to A", async () => {
  let reject!: (error: Error) => void;
  vi.stubGlobal("fetch", vi.fn(() => new Promise((_, fail) => { reject = fail; })));
  bindScope({ id: "alice", revision: 1 });
  useChatStore.getState().reset();
  const pending = useChatStore.getState().sendMessage("hello");
  bindScope({ id: "bob", revision: 2 });
  useChatStore.getState().reset();
  bindScope({ id: "alice", revision: 3 });
  reject(new Error("old failure"));
  await pending;
  expect(useChatStore.getState().sendError).toBeNull();
});

it("does not send a reading recovery request after switching", async () => {
  let reject!: (error: Error) => void;
  const fetch = vi.fn(() => new Promise((_, fail) => { reject = fail; }));
  vi.stubGlobal("fetch", fetch);
  bindScope({ id: "alice", revision: 1 });
  useReaderStore.setState({ bookId: "book", totalParagraphs: 10, userPosition: 1 });
  const pending = useReaderStore.getState().syncPosition(2);
  useReaderStore.getState().closeBook();
  bindScope({ id: "bob", revision: 2 });
  reject(new Error("offline"));
  await pending;
  expect(fetch).toHaveBeenCalledTimes(1);
});

it("scopes JSON writes and 204 deletes with the selection revision", async () => {
  const fetch = vi.fn().mockResolvedValue(new Response(JSON.stringify({ event_id: "ok" })));
  vi.stubGlobal("fetch", fetch);
  bindScope({ id: "alice", revision: 7 });
  await postChat("hello");
  await deleteBookmark("mark");
  expect(fetch.mock.calls.map(([url]) => url)).toEqual([
    "/api/characters/alice/chat", "/api/characters/alice/bookmarks/mark",
  ]);
  for (const [, init] of fetch.mock.calls) {
    expect(new Headers(init.headers).get("X-Nyx-Selection-Revision")).toBe("7");
  }
});
