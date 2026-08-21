import { activityAnnouncement } from "../lib/activityResult";
import { useActivityStore } from "../stores/activityStore";
import { useAnnounceStore } from "../stores/announceStore";
import { useChatStore } from "../stores/chatStore";
import { useDesireStore } from "../stores/desireStore";
import { useEventStore } from "../stores/eventStore";
import { useInnerLifeStore } from "../stores/innerLifeStore";
import { useMemoryStore } from "../stores/memoryStore";
import type { SseEvent } from "../types/api";

// 事件 → store 路由（01-sse §4.1）。
// 溯源面板要「SSE 全部」，故先无条件 eventStore.record(e)（补齐现状只记 11 类未消费的漏）。
// desire/activity/memory 事件只带 id（{"desire_id"}/{"memory_id"}/{"activity_id"}），
// 面板收到就 refresh() 重拉快照；clock_tick/observation_state/reflection 仅 record 不路由。
export function dispatchEvent(e: SseEvent): void {
  useEventStore.getState().record(e);
  switch (e.event) {
    case "speak":
      return useChatStore.getState().addSpeak(e);
    case "ask":
      return useChatStore.getState().addAsk(e);
    case "think":
      return useChatStore.getState().addThink(e);
    case "mutter":
      useChatStore.getState().addMutter(e);
      useAnnounceStore.getState().announce("mutter", e.content);
      return;
    case "initiate_chat":
      return useChatStore.getState().addInitiateChat(e);
    case "user_message":
      return useChatStore.getState().addUserMessage(e);
    case "emotion_update":
      return useInnerLifeStore.getState().updateEmotion(e);
    case "memory_created":
    case "memory_promoted":
      useMemoryStore.getState().refresh();
      return;
    case "desire_generated":
    case "desire_satisfied":
    case "desire_expired":
      useDesireStore.getState().refresh();
      return;
    case "activity_start":
    case "activity_interrupted":
      useActivityStore.getState().refresh();
      return;
    case "activity_end": {
      // 完成后主动冒一句：refresh 重拉快照后，按 activity_id 找到刚完成的活动，
      // 有产出就 announce("activity", …)（activityAnnouncement 见 lib/activityResult）。
      void useActivityStore.getState().refresh().then(() => {
        const id = e.activity_id;
        if (typeof id !== "string") return;
        const a = useActivityStore.getState().data?.schedule.find((x) => x.id === id);
        if (a === undefined) return;
        const text = activityAnnouncement(a);
        if (text !== null) useAnnounceStore.getState().announce("activity", text);
      });
      return;
    }
  }
}
