import { useChatStore } from "../stores/chatStore";
import { useEventStore } from "../stores/eventStore";
import { useInnerLifeStore } from "../stores/innerLifeStore";
import type { SseEvent } from "../types/api";

// 事件 → store 路由（01-sse §4.1）：前 7 类消费，其余 11 类 eventStore.record 兜底
export function dispatchEvent(e: SseEvent): void {
  switch (e.event) {
    case "speak":
      return useChatStore.getState().addSpeak(e);
    case "ask":
      return useChatStore.getState().addAsk(e);
    case "think":
      return useChatStore.getState().addThink(e);
    case "mutter":
      return useChatStore.getState().addMutter(e);
    case "initiate_chat":
      return useChatStore.getState().addInitiateChat(e);
    case "user_message":
      return useChatStore.getState().addUserMessage(e);
    case "emotion_update":
      return useInnerLifeStore.getState().updateEmotion(e);
    default:
      return useEventStore.getState().record(e);
  }
}
