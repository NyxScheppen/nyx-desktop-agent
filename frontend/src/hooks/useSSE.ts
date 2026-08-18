import { useEffect, useState } from "react";
import { BASE_URL } from "../api/client";
import type { ConnectionState, SseEvent } from "../types/api";

// 后端 enums.py EventType 的 18 个 snake_case 值。
// EventSource 的 onmessage 只收无 `event:` 行的默认消息；后端每条都带
// `event: {type.value}` 行（main.py），命名事件必须按类型 addEventListener 才能收到。
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
  "memory_created",
  "memory_promoted",
  "desire_generated",
  "desire_satisfied",
  "desire_expired",
  "activity_start",
  "activity_end",
  "activity_interrupted",
];

export function useSSE(dispatch: (e: SseEvent) => void): ConnectionState {
  const [status, setStatus] = useState<ConnectionState>("connecting");

  useEffect(() => {
    const source = new EventSource(`${BASE_URL}/api/events`);

    const onEvent = (type: string) => (event: MessageEvent) => {
      try {
        const data: unknown = JSON.parse(event.data);
        if (typeof data !== "object" || data === null) {
          console.error("SSE 帧 data 非对象，跳过", event.data);
          return;
        }
        const rec = data as Record<string, unknown>;
        if (
          typeof rec.event_id !== "string" ||
          typeof rec.correlation_id !== "string"
        ) {
          console.error("SSE 帧缺 event_id/correlation_id，跳过", event.data);
          return;
        }
        dispatch({ ...rec, event: type } as SseEvent);
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
