import { useEffect, useState } from "react";
import { BASE_URL } from "../api/client";
import { parseSseEvent, type ConnectionState, type SseEvent } from "../types/api";

// 后端 enums.py EventType 的 24 个 snake_case 值。
// 命名事件（带 event: 行）只能按类型 addEventListener 收到，onmessage 收不到。
// 前向兼容边界：后端新增 EventType 必须同步此数组 + types/api.ts 判别联合 +
// dispatchEvent 分发表，否则新类型帧被浏览器静默丢弃（01-sse §4）。
const EVENT_TYPES = [
  "user_message",
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

export function useSSE(dispatch: (e: SseEvent) => void): ConnectionState {
  const [status, setStatus] = useState<ConnectionState>("connecting");

  useEffect(() => {
    // An HMR/StrictMode effect replacement runs cleanup before the new source
    // opens; publish the replacement's connecting state immediately so the
    // old cleanup cannot leave the UI showing a stale "closed" state.
    setStatus("connecting");
    const source = new EventSource(`${BASE_URL}/api/events`);
    const seenEventIds = new Set<string>();

    const onEvent = (type: string) => (event: MessageEvent) => {
      try {
        const data: unknown = JSON.parse(event.data);
        if (typeof data !== "object" || data === null) {
          console.error("SSE 帧 data 非对象，跳过", event.data);
          return;
        }
        const parsed = parseSseEvent(type, data);
        if (parsed === null) {
          console.error("SSE 帧结构非法，跳过", event.data);
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
        console.error("SSE 帧解析失败，跳过", event.data, err);
      }
    };

    source.onopen = () => setStatus("open");
    source.onerror = () => setStatus("connecting");
    for (const type of EVENT_TYPES) {
      source.addEventListener(type, onEvent(type));
    }

    return () => {
      source.close();
      setStatus("closed");
    };
  }, [dispatch]);

  return status;
}
