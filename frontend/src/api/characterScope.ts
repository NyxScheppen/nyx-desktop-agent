import type { StoreApi } from "zustand";

export type CharacterScope = { id: string; revision: number; generation: number };
let scope: CharacterScope | null = null;
let generation = 0;
let switching = false;
const reads = new Set<AbortController>();

export function captureScope(): CharacterScope | null { return scope; }
export function isScopeCurrent(captured: CharacterScope | null): boolean {
  return !switching && captured === scope;
}
export function invalidateScope(): void {
  switching = true;
  generation += 1;
  scope = null;
  for (const controller of reads) controller.abort();
  reads.clear();
}
export function bindScope(next: { id: string; revision: number } | null): void {
  invalidateScope();
  switching = false;
  scope = next === null ? null : { ...next, generation };
}
export function scopedRequest(url: string, init?: RequestInit): {
  url: string; init: RequestInit; release: () => void;
} {
  const business = /\/api\/(?!characters(?:\/|$)|observe(?:\?|$)|ready(?:\?|$))/.test(url);
  if (!business) return { url, init: init ?? {}, release: () => {} };
  if (switching) throw new Error("角色正在切换");
  const selected = scope;
  if (selected === null) return { url, init: init ?? {}, release: () => {} };
  const headers = new Headers(init?.headers);
  const method = init?.method ?? "GET";
  if (method !== "GET") headers.set("X-Nyx-Selection-Revision", String(selected.revision));
  const controller = method === "GET" ? new AbortController() : null;
  if (controller) reads.add(controller);
  return {
    url: url.replace("/api/", `/api/characters/${encodeURIComponent(selected.id)}/`),
    init: { ...init, headers, signal: init?.signal ?? controller?.signal },
    release: () => { if (controller) reads.delete(controller); },
  };
}

// Capture at action entry; stale success, failure and finally paths share this guard.
export function scopedSetter<T>(set: StoreApi<T>["setState"]): StoreApi<T>["setState"] {
  const captured = captureScope();
  return ((...args: Parameters<typeof set>) => {
    if (isScopeCurrent(captured)) (set as (...a: Parameters<typeof set>) => void)(...args);
  }) as typeof set;
}
