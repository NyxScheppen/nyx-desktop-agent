import { afterEach, expect, it, vi } from "vitest";
import { bindScope } from "../src/api/characterScope";
import { useDesireStore } from "../src/stores/desireStore";

afterEach(() => { bindScope(null); vi.unstubAllGlobals(); });

const flush = async () => { for (let i = 0; i < 20; i++) await Promise.resolve(); };

it("a burst during a snapshot fetch queues one trailing fetch with the latest state", async () => {
  bindScope({ id: "a", revision: 1 });
  const replies: ((value: Response) => void)[] = [];
  const fetch = vi.fn(() => new Promise<Response>((resolve) => replies.push(resolve)));
  vi.stubGlobal("fetch", fetch);
  const first = useDesireStore.getState().refresh();
  await flush();
  const rest = Array.from({ length: 100 }, () => useDesireStore.getState().refresh());
  expect(fetch).toHaveBeenCalledTimes(1);
  replies[0](new Response(JSON.stringify({ version: 1 })));
  await flush();
  expect(fetch).toHaveBeenCalledTimes(2);
  replies[1](new Response(JSON.stringify({ version: 2 })));
  await Promise.all([first, ...rest]);
  expect(fetch).toHaveBeenCalledTimes(2);
  expect(useDesireStore.getState().data).toEqual({ version: 2 });
});

it("A→B→A drops old queued refreshes and old finalizers cannot clear new work", async () => {
  bindScope({ id: "a", revision: 1 });
  const replies: ((value: Response) => void)[] = [];
  const fetch = vi.fn(() => new Promise<Response>((resolve) => replies.push(resolve)));
  vi.stubGlobal("fetch", fetch);
  const old = useDesireStore.getState().refresh();
  await flush();
  void useDesireStore.getState().refresh(); // Queued on the old role.
  bindScope({ id: "b", revision: 2 });
  bindScope({ id: "a", revision: 3 });
  useDesireStore.setState({ data: null, error: null });
  const current = useDesireStore.getState().refresh();
  await flush();
  replies[0](new Response(JSON.stringify({ version: "old" })));
  await old;
  expect(useDesireStore.getState().data).toBeNull();
  const trailing = useDesireStore.getState().refresh();
  expect(fetch).toHaveBeenCalledTimes(2);
  replies[1](new Response(JSON.stringify({ version: "new" })));
  await flush();
  expect(fetch).toHaveBeenCalledTimes(3);
  replies[2](new Response(JSON.stringify({ version: "latest" })));
  await Promise.all([current, trailing]);
  expect(useDesireStore.getState().data).toEqual({ version: "latest" });
});
