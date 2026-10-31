import { afterEach, expect, it, vi } from "vitest";
import { useCharacterStore } from "../src/stores/characterStore";
import { captureScope, bindScope } from "../src/api/characterScope";
import { useConnectionStore } from "../src/stores/connectionStore";

afterEach(() => { vi.unstubAllGlobals(); bindScope(null); localStorage.clear(); });

it("loads the persisted selection and serializes switches", async () => {
  let id = "nyx", revision = 0;
  const selected: string[] = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    if (url.endsWith("/current")) {
      id = JSON.parse(String(init?.body)).character_id;
      selected.push(id); revision++;
      return new Response(JSON.stringify({ current_id: id, selection_revision: revision }));
    }
    return new Response(JSON.stringify({ current_id: id, selection_revision: revision, limit: 4,
      characters: ["nyx", "alice"].map((id) => ({ id, name: id, status: "RUNNING", assets: {}, error: null })),
    }));
  }));
  await useCharacterStore.getState().initialize();
  await Promise.all([useCharacterStore.getState().select("alice"), useCharacterStore.getState().select("nyx")]);
  expect(selected).toEqual(["alice", "nyx"]);
  expect(captureScope()?.revision).toBe(2);
});

it("persists drafts by role and remembers an open book immediately", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({
    current_id: "alice", selection_revision: 1, limit: 4,
    characters: [{ id: "alice", name: "Alice", status: "RUNNING", assets: {}, error: null }],
  }))));
  await useCharacterStore.getState().initialize();
  useCharacterStore.getState().setDraft("Alice's draft");
  const { useReaderStore } = await import("../src/stores/readerStore");
  useReaderStore.setState({ bookId: "alice-book" });
  const saved = JSON.parse(localStorage.getItem("nyx.characterUi") ?? "{}");
  expect(saved.drafts.alice).toBe("Alice's draft");
  expect(saved.lastBooks.alice).toBe("alice-book");
  expect(saved.drafts.nyx).toBeUndefined();
});

it("does not adopt a stale registry poll started during a switch", async () => {
  let id = "nyx", revision = 0;
  let releaseSwitch!: () => void;
  let releasePoll!: () => void;
  const switching = new Promise<void>((resolve) => { releaseSwitch = resolve; });
  const poll = new Promise<void>((resolve) => { releasePoll = resolve; });
  vi.stubGlobal("fetch", vi.fn(async (url: string) => {
    if (url.endsWith("/books")) return new Response("[]");
    if (url.endsWith("/current")) {
      await switching;
      id = "alice"; revision = 1;
      return new Response(JSON.stringify({ current_id: id, selection_revision: revision }));
    }
    const snapshot = { current_id: id, selection_revision: revision, limit: 4,
      characters: ["nyx", "alice"].map((id) => ({ id, name: id, status: "RUNNING", assets: {}, error: null })),
    };
    if (useCharacterStore.getState().switching && id === "nyx") await poll;
    return new Response(JSON.stringify(snapshot));
  }));
  await useCharacterStore.getState().initialize();
  const selection = useCharacterStore.getState().select("alice");
  await vi.waitFor(() => expect(useCharacterStore.getState().switching).toBe(true));
  const refresh = useCharacterStore.getState().refresh();
  releaseSwitch();
  await selection;
  releasePoll();
  await refresh;
  expect(captureScope()?.id).toBe("alice");
  expect(useCharacterStore.getState().registry?.selection_revision).toBe(1);
});

it("does not adopt a registry response from a credential that was rejected", async () => {
  useCharacterStore.setState(useCharacterStore.getInitialState());
  let release!: () => void;
  const pending = new Promise<void>((resolve) => { release = resolve; });
  useConnectionStore.setState({ token: "old-token", required: false });
  vi.stubGlobal("fetch", vi.fn(async () => {
    await pending;
    return new Response(JSON.stringify({ current_id: "alice", selection_revision: 1,
      characters: [{ id: "alice", name: "Alice", status: "RUNNING", assets: {}, error: null }],
    }));
  }));
  const refresh = useCharacterStore.getState().initialize();
  useConnectionStore.setState({ required: true });
  release();
  await refresh;
  expect(useCharacterStore.getState().registry).toBeNull();
  expect(captureScope()).toBeNull();
});

it("does not adopt a selection response after its credential is rejected", async () => {
  useCharacterStore.setState(useCharacterStore.getInitialState());
  useConnectionStore.setState({ token: "old-token", required: false });
  let registryCalls = 0;
  let release!: () => void;
  const pending = new Promise<void>((resolve) => { release = resolve; });
  vi.stubGlobal("fetch", vi.fn(async (url: string) => {
    if (url.endsWith("/current")) return new Response("{}");
    registryCalls += 1;
    if (registryCalls === 2) await pending;
    const current = registryCalls === 1 ? "nyx" : "alice";
    return new Response(JSON.stringify({ current_id: current, selection_revision: registryCalls - 1,
      characters: [current].map((id) => ({ id, name: id, status: "RUNNING", assets: {}, error: null })),
    }));
  }));
  await useCharacterStore.getState().initialize();
  const selection = useCharacterStore.getState().select("alice");
  await vi.waitFor(() => expect(registryCalls).toBe(2));
  useConnectionStore.setState({ required: true });
  release();
  await selection;
  expect(useCharacterStore.getState().registry?.current_id).toBe("nyx");
  expect(useCharacterStore.getState().switching).toBe(false);
  expect(captureScope()).toBeNull();
});
