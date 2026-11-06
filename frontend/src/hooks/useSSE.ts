import { captureScope, isScopeCurrent } from "../api/characterScope";
import { useEffect, useState } from "react";
import { BASE_URL } from "../api/client";
import { useConnectionStore } from "../stores/connectionStore";
import { parseSseEvent, type ConnectionState, type SseEvent } from "../types/api";

// 后端 enums.py EventType 的 snake_case 值。
// 命名事件按 event: 行解析；未知类型不会进入分发。
// 前向兼容边界：后端新增 EventType 必须同步此数组 + types/api.ts 判别联合 +
// dispatchEvent 分发表，否则新类型帧被浏览器静默丢弃。
const EVENT_TYPES = [
  "user_message",
  "annotation_feedback",
  "clock_tick",
  "observation_state",
  "speak",
  "ask",
  "think",
  "mutter",
  "initiate_chat",
  "emotion_update",
  "reflection",
  "reflection_done",
  "memory_created",
  "memory_promoted",
  "scene_memory_requested",
  "desire_generated",
  "desire_satisfied",
  "desire_expired",
  "activity_start",
  "activity_end",
  "activity_interrupted",
  "task_updated",
  "reading_mutter",
  "reading_question",
  "reading_association",
  "reading_progress",
];

const MAX_SEEN_EVENT_IDS = 4096;

export function useSSE(dispatch: (e: SseEvent) => void, selection?: { id: string; generation: number } | null): ConnectionState {
  const [status, setStatus] = useState<ConnectionState>("connecting");

  const token = useConnectionStore((s) => s.token);

  useEffect(() => {
    if (selection === null) { setStatus("closed"); return; }
    let disposed = false;
    const scope = captureScope();
    const active = () => !disposed && isScopeCurrent(scope);
    const controller = new AbortController();
    let reader: ReadableStreamDefaultReader<Uint8Array> | undefined;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let stopWaiting = () => {};
    let cursor = "";
    setStatus("connecting");
    const seenEventIds = new Set<string>();

    const onEvent = (type: string, text: string) => {
      if (disposed || !isScopeCurrent(scope)) return;
      try {
        const data: unknown = JSON.parse(text);
        if (typeof data !== "object" || data === null) {
          console.error("SSE 帧 data 非对象，跳过", text);
          return;
        }
        if (selection && (data as Record<string, unknown>).character_id !== selection.id) return;
        const parsed = parseSseEvent(type, data);
        if (parsed === null) {
          console.error("SSE 帧结构非法，跳过", text);
          return;
        }
        if (seenEventIds.has(parsed.event_id)) return;
        seenEventIds.add(parsed.event_id);
        if (seenEventIds.size > MAX_SEEN_EVENT_IDS) {
          const oldest = seenEventIds.values().next().value;
          if (typeof oldest === "string") seenEventIds.delete(oldest);
        }
        dispatch(parsed);
      } catch (err) {
        console.error("SSE 帧解析失败，跳过", text, err);
      }
    };

    const connect = async () => {
      while (active()) {
        try {
          const headers = new Headers();
          if (token) headers.set("Authorization", `Bearer ${token}`);
          if (cursor) headers.set("Last-Event-ID", cursor);
          const response = await fetch(selection
            ? `${BASE_URL}/api/characters/${encodeURIComponent(selection.id)}/events`
            : `${BASE_URL}/api/events`, { headers, signal: controller.signal });
          if (!active()) { await response.body?.cancel(); return; }
          if (response.status === 401) {
            if (useConnectionStore.getState().token === token) {
              useConnectionStore.setState({ required: true });
            }
            setStatus("closed");
            return;
          }
          if (!response.ok || !response.body ||
              !response.headers.get("content-type")?.startsWith("text/event-stream")) {
            throw new Error("SSE connection failed");
          }
          setStatus("open");
          reader = response.body.getReader();
          const decoder = new TextDecoder();
          let buffer = "";
          let type = "";
          let id: string | undefined;
          let data: string[] = [];
          while (active()) {
            const chunk = await reader.read();
            if (chunk.done) break;
            buffer += decoder.decode(chunk.value, { stream: true });
            let newline: number;
            while (active() && (newline = buffer.indexOf("\n")) >= 0) {
              const line = buffer.slice(0, newline).replace(/\r$/, "");
              buffer = buffer.slice(newline + 1);
              if (line === "") {
                if (data.length) {
                  if (id !== undefined) cursor = id;
                  if (EVENT_TYPES.includes(type)) onEvent(type, data.join("\n"));
                }
                type = ""; id = undefined; data = [];
              } else if (!line.startsWith(":")) {
                const colon = line.indexOf(":");
                const field = colon < 0 ? line : line.slice(0, colon);
                const value = colon < 0 ? "" : line.slice(colon + 1).replace(/^ /, "");
                if (field === "event") type = value;
                if (field === "data") data.push(value);
                if (field === "id" && !value.includes("\0")) id = value;
              }
            }
          }
        } catch {
          // Network interruption resumes from the last complete frame.
        } finally {
          reader?.releaseLock();
          reader = undefined;
        }
        if (!active()) return;
        setStatus("connecting");
        await new Promise<void>((resolve) => {
          stopWaiting = resolve;
          timer = setTimeout(resolve, 1000);
        });
      }
    };
    void connect();

    return () => {
      disposed = true;
      controller.abort();
      void reader?.cancel();
      clearTimeout(timer);
      stopWaiting();
      setStatus("closed");
    };
  }, [dispatch, selection?.id, selection?.generation, selection === null, token]);

  return status;
}
