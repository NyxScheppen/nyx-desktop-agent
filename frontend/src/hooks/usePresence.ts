import { useEffect, useRef } from "react";
import { invoke } from "@tauri-apps/api/core";
import { postObserve } from "../api/client";
import type { Presence } from "../types/api";

const OBSERVE_INTERVAL_MS = 30_000;

type PresenceSnapshot = {
  presence: Presence;
  windowTitle: string;
  idleMs: number;
};

export function classifyPresence(idleMs: number): Presence {
  if (idleMs < 30_000) return "online";
  if (idleMs < 300_000) return "busy";
  return "away";
}

function sameSnapshot(a: PresenceSnapshot | null, b: PresenceSnapshot): boolean {
  return a?.presence === b.presence && a.windowTitle === b.windowTitle;
}

export function usePresence(): void {
  const lastWebInputAt = useRef(Date.now());

  useEffect(() => {
    let disposed = false;
    let sending = false;
    let pending: PresenceSnapshot | null = null;
    let lastSent: PresenceSnapshot | null = null;

    const onInput = () => {
      lastWebInputAt.current = Date.now();
    };
    window.addEventListener("keydown", onInput);
    window.addEventListener("mousemove", onInput);

    const flush = async () => {
      if (sending) return;
      sending = true;
      while (!disposed && pending !== null) {
        const next = pending;
        pending = null;
        if (sameSnapshot(lastSent, next)) continue;
        try {
          await postObserve(next.presence, next.windowTitle, next.idleMs / 1000);
        } catch (err) {
          console.error("presence 上报失败", err);
          if (pending === null) pending = next;
          break;
        }
        lastSent = next;
      }
      sending = false;
    };

    const enqueue = (snapshot: PresenceSnapshot) => {
      if (sameSnapshot(lastSent, snapshot) && pending === null) return;
      pending = snapshot;
      void flush();
    };

    const sample = async () => {
      const now = Date.now();
      let idleMs: number;
      let windowTitle: string;
      try {
        [idleMs, windowTitle] = await invoke<[number, string]>("sample_presence");
      } catch {
        idleMs = Math.max(0, now - lastWebInputAt.current);
        windowTitle = "";
      }
      const normalizedIdle = Number.isFinite(idleMs) ? Math.max(0, idleMs) : 0;
      enqueue({
        presence: classifyPresence(normalizedIdle),
        windowTitle,
        idleMs: normalizedIdle,
      });
    };

    void sample();
    const timer = setInterval(() => void sample(), OBSERVE_INTERVAL_MS);

    return () => {
      disposed = true;
      window.removeEventListener("keydown", onInput);
      window.removeEventListener("mousemove", onInput);
      clearInterval(timer);
    };
  }, []);
}
