import { captureScope } from "../api/characterScope";
import { useEffect, useRef } from "react";
import { invoke } from "@tauri-apps/api/core";
import { postObserve } from "../api/client";
import type { ConnectionState, Presence } from "../types/api";

const OBSERVE_INTERVAL_MS = 30_000;
const OBSERVE_TIMEOUT_MS = 10_000;

type PresenceSnapshot = {
  presence: Presence;
  windowTitle: string;
  idleMs: number;
  sampledAt: number;
  sampleId: string;
  characterId?: string;
  selectionRevision?: number;
};

export function classifyPresence(idleMs: number): Presence {
  if (idleMs < 30_000) return "online";
  if (idleMs < 300_000) return "busy";
  return "away";
}

function sameSnapshot(a: PresenceSnapshot | null, b: PresenceSnapshot): boolean {
  return a?.characterId === b.characterId && a?.selectionRevision === b.selectionRevision && a?.presence === b.presence && a.windowTitle === b.windowTitle &&
    b.sampledAt >= a.sampledAt;
}

export function usePresence(connection: ConnectionState): void {
  const lastWebInputAt = useRef(performance.now());
  const wake = useRef<() => void>(() => {});
  useEffect(() => { if (connection === "open") wake.current(); }, [connection]);

  useEffect(() => {
    let disposed = false;
    let sending = false;
    const pending: PresenceSnapshot[] = [];
    let lastSent: PresenceSnapshot | null = null;
    let sampleSequence = 0;
    let controller: AbortController | null = null;

    const onInput = () => {
      lastWebInputAt.current = performance.now();
    };
    window.addEventListener("keydown", onInput);
    window.addEventListener("mousemove", onInput);

    const flush = async () => {
      if (sending) return;
      sending = true;
      while (!disposed && pending.length > 0) {
        const next = pending[0];
        if (sameSnapshot(lastSent, next)) { pending.shift(); continue; }
        controller = new AbortController();
        const signal = controller.signal;
        let timeout: ReturnType<typeof setTimeout> | undefined;
        let onAbort: (() => void) | undefined;
        try {
          // Race also releases single-flight if a transport fails to settle on abort.
          await Promise.race([
            postObserve({
              sample_id: next.sampleId,
              character_id: next.characterId,
              selection_revision: next.selectionRevision,
              presence: next.presence,
              window_title: next.windowTitle,
              idle_seconds: next.idleMs / 1000,
              sampled_at: next.sampledAt,
            }, signal),
            new Promise<never>((_, reject) => {
              onAbort = () => reject(new Error("presence request aborted"));
              signal.addEventListener("abort", onAbort, { once: true });
              timeout = setTimeout(() => {
                controller?.abort();
                reject(new Error("presence request timed out"));
              }, OBSERVE_TIMEOUT_MS);
            }),
          ]);
        } catch (err) {
          if (!disposed) console.error("presence 上报失败", err);
          lastSent = null;
          break;
        } finally {
          clearTimeout(timeout);
          if (onAbort !== undefined) signal.removeEventListener("abort", onAbort);
          controller = null;
        }
        pending.shift();
        lastSent = next;
      }
      sending = false;
    };

    const enqueue = (snapshot: PresenceSnapshot) => {
      if (sameSnapshot(pending[pending.length - 1] ?? lastSent, snapshot)) { void flush(); return; }
      pending.push(snapshot);
      void flush();
    };

    const sample = async () => {
      if (pending.length >= 64) { void flush(); return; }
      const scope = captureScope();
      const sequence = ++sampleSequence;
      const now = Date.now();
      const sampledMonotonic = performance.now();
      let idleMs: number;
      let windowTitle: string;
      try {
        const native: unknown = await invoke("sample_presence");
        if (
          !Array.isArray(native) || native.length !== 2 ||
          typeof native[0] !== "number" || typeof native[1] !== "string"
        ) {
          throw new Error("invalid native presence tuple");
        }
        [idleMs, windowTitle] = native as [number, string];
        if (!Number.isFinite(idleMs) || idleMs < 0 || idleMs > now) {
          throw new Error("invalid native presence sample");
        }
      } catch {
        idleMs = Math.max(0, sampledMonotonic - lastWebInputAt.current);
        windowTitle = "";
      }
      if (disposed || sequence !== sampleSequence) return;
      enqueue({
        sampleId: crypto.randomUUID(),
        characterId: scope?.id,
        selectionRevision: scope?.revision,
        presence: classifyPresence(idleMs),
        windowTitle: Array.from(windowTitle).slice(0, 512).join(""),
        idleMs,
        sampledAt: now / 1000,
      });
    };

    wake.current = () => { lastSent = null; void flush(); void sample(); };
    void sample();
    const timer = setInterval(() => void sample(), OBSERVE_INTERVAL_MS);
    const onFocus = () => void sample();
    const onVisible = () => {
      if (document.visibilityState === "visible") void sample();
    };
    window.addEventListener("focus", onFocus);
    document.addEventListener("visibilitychange", onVisible);

    return () => {
      disposed = true;
      wake.current = () => {};
      controller?.abort();
      window.removeEventListener("keydown", onInput);
      window.removeEventListener("mousemove", onInput);
      window.removeEventListener("focus", onFocus);
      document.removeEventListener("visibilitychange", onVisible);
      clearInterval(timer);
    };
  }, []);
}
