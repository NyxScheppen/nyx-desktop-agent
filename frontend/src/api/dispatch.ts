import { activityAnnouncement } from "../lib/activityResult";
import { useActivityStore } from "../stores/activityStore";
import { useAnnounceStore } from "../stores/announceStore";
import { useChatStore } from "../stores/chatStore";
import { useDesireStore } from "../stores/desireStore";
import { useInnerLifeStore } from "../stores/innerLifeStore";
import { useMemoryStore } from "../stores/memoryStore";
import type { SseEvent } from "../types/api";

// 事件 → store 路由（01-sse §4.1）。
// desire/activity 事件只带 id（{"desire_id"}/{"activity_id"}），
// 面板收到就 refresh() 重拉快照；clock_tick/observation_state/reflection 不路由。
export function dispatchEvent(e: SseEvent): void {
  switch (e.event) {
    case "speak":
      return useChatStore.getState().addSpeak(e);
    case "ask":
      return useChatStore.getState().addAsk(e);
    case "think":
      return useChatStore.getState().addThink(e);
    case "mutter":
      // 碎碎念改悬浮气泡（08 §3）：与 reading_mutter/reflection_done 统一走 announce("mutter")。
      return useAnnounceStore.getState().announce("mutter", e.content);
    case "initiate_chat":
      return useChatStore.getState().addInitiateChat(e);
    case "user_message":
      return useChatStore.getState().addUserMessage(e);
    case "emotion_update":
      // 载荷只带 {valence, arousal, emotion}（12-inner-life），能量/性格/三观不随帧下发；
      // 而 emotion_update 恰好在能量(ACTIVITY_END)/性格三观(REFLECTION)可能变化的那一刻发出，
      // 故顺带 refreshState() 重拉全量快照，带新 EnergyBar/BigFiveChart/ValuesChart。
      useInnerLifeStore.getState().updateEmotion(e);
      void useInnerLifeStore.getState().refreshState();
      return;
    case "desire_generated":
    case "desire_satisfied":
    case "desire_expired":
      useDesireStore.getState().refresh();
      return;
    case "memory_created":
    case "memory_promoted":
      useMemoryStore.getState().refresh();
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
    case "reading_mutter":
      // 读书碎碎念归入悬浮气泡（08 §2.3/§3），与全局 mutter 同一渲染路径。
      return useAnnounceStore.getState().announce("mutter", e.content);
    case "reading_question":
    case "reading_association":
      // 读书提问/联想并进对话（08 §2.3）：并进对话转录，不再走 readerStore 气泡流。
      return useChatStore.getState().addReadingTurn(e);
    case "reflection_done": {
      // 反思完成：长期欲望（add_long_term 不发 desire_generated）刷新；
      // story 真新增才冒气泡（去重跳过则静默刷新，不打扰）。
      void useDesireStore.getState().refresh();
      if (e.story_is_new) {
        const preview =
          e.story.length > 30 ? `${e.story.slice(0, 30)}…` : e.story;
        useAnnounceStore.getState().announce(
          "mutter",
          `小狐狸我呀，反思了一下：${preview}`,
        );
      }
      return;
    }
  }
}
