import { useEffect, useState } from "react";
import { BASE_URL } from "../api/client";
import type { ConnectionState, SseEvent } from "../types/api";

// 后端 enums.py EventType 的 21 个 snake_case 值。
// 命名事件（带 event: 行）只能按类型 addEventListener 收到，onmessage 收不到。
// 前向兼容边界：后端新增 EventType 必须同步此数组 + types/api.ts 判别联合 +
// dispatchEvent 分发表，否则新类型帧被浏览器静默丢弃（01-sse §4）。
const EVENT_TYPES = [
  "user_message",
  "user_material",
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
  "desire_generated",
  "desire_satisfied",
  "desire_expired",
  "activity_start",
  "activity_end",
  "activity_interrupted",
  "exploration_step",
  "encounter_start",
  "encounter_choice",
  "encounter_end",
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
        // 信任边界：帧头已校验，其余键形状交给 store action 运行时收窄；
        // 判别联合只兜编译期契约（键名错位在此放行，store 里拦）。
        dispatch({ ...rec, event: type } as unknown as SseEvent);
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
