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
};

export function classifyPresence(idleMs: number): Presence {
  if (idleMs < 30_000) return "online";
  if (idleMs < 300_000) return "busy";
  return "away";
}

function sameSnapshot(a: PresenceSnapshot | null, b: PresenceSnapshot): boolean {
  return a?.presence === b.presence && a.windowTitle === b.windowTitle &&
    b.sampledAt >= a.sampledAt;
}

export function usePresence(connection: ConnectionState): void {
  const lastWebInputAt = useRef(performance.now());

  useEffect(() => {
    let disposed = false;
    let sending = false;
    let pending: PresenceSnapshot | null = null;
    let lastSent: PresenceSnapshot | null = null;
    let sampleSequence = 0;
    let controller: AbortController | null = null;

    const onInput = () => {
      lastWebInputAt.current = performance.now();
    };
    window.addEventListener("keydown", onInput);
    window.addEventListener("mousemove", onInput);
    if (connection !== "open") {
      return () => {
        window.removeEventListener("keydown", onInput);
        window.removeEventListener("mousemove", onInput);
      };
    }

    const flush = async () => {
      if (sending) return;
      sending = true;
      while (!disposed && pending !== null) {
        const next = pending;
        pending = null;
        if (sameSnapshot(lastSent, next)) continue;
        controller = new AbortController();
        const signal = controller.signal;
        let timeout: ReturnType<typeof setTimeout> | undefined;
        let onAbort: (() => void) | undefined;
        try {
          // Race also releases single-flight if a transport fails to settle on abort.
          await Promise.race([
            postObserve({
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
          if (pending === null) pending = next;
          break;
        } finally {
          clearTimeout(timeout);
          if (onAbort !== undefined) signal.removeEventListener("abort", onAbort);
          controller = null;
        }
        lastSent = next;
      }
      sending = false;
    };

    const enqueue = (snapshot: PresenceSnapshot) => {
      if (!sending && sameSnapshot(lastSent, snapshot) && pending === null) return;
      pending = snapshot;
      void flush();
    };

    const sample = async () => {
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
        presence: classifyPresence(idleMs),
        windowTitle: Array.from(windowTitle).slice(0, 512).join(""),
        idleMs,
        sampledAt: now / 1000,
      });
    };

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
      controller?.abort();
      window.removeEventListener("keydown", onInput);
      window.removeEventListener("mousemove", onInput);
      window.removeEventListener("focus", onFocus);
      document.removeEventListener("visibilitychange", onVisible);
      clearInterval(timer);
    };
  }, [connection]);
}
