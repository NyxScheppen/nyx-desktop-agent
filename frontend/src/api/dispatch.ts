import { useBehaviorStore } from "../stores/behaviorStore";
import { activityAnnouncement } from "../lib/activityResult";
import { useActivityStore } from "../stores/activityStore";
import { useAnnounceStore } from "../stores/announceStore";
import { useChatStore } from "../stores/chatStore";
import { useDesireStore } from "../stores/desireStore";
import { useInnerLifeStore } from "../stores/innerLifeStore";
import { useMemoryStore } from "../stores/memoryStore";
import { useReaderStore } from "../stores/readerStore";
import type { SseEvent } from "../types/api";
import { captureScope, isScopeCurrent } from "./characterScope";

export function synchronizeAfterReplay(): void {
  void useBehaviorStore.getState().refresh();
  void useInnerLifeStore.getState().refreshState();
  void useActivityStore.getState().refresh();
  void useDesireStore.getState().refresh();
  void useMemoryStore.getState().refresh();
  void useChatStore.getState().loadHistory();
}

// 事件 → store 路由。
// desire/activity 事件只带 id（{"desire_id"}/{"activity_id"}），
// 面板收到就 refresh() 重拉快照；clock_tick/observation_state/reflection 不路由。
export function dispatchEvent(e: SseEvent, replaying = false): void {
  // Historical chat still restores coverage/pending state. Snapshot modules are
  // synchronized once at the boundary; old announcements should not reappear.
  if (replaying && !["speak", "ask", "think", "initiate_chat", "user_message",
    "annotation_feedback", "reading_question", "reading_progress"].includes(e.event)) return;
  switch (e.event) {
    case "behavior_reflection":
    case "behavior_progress":
    case "research_progress":
      void useBehaviorStore.getState().refresh();
      return;
    case "speak":
      return useChatStore.getState().addSpeak(e);
    case "ask":
      return useChatStore.getState().addAsk(e);
    case "think":
      return useChatStore.getState().addThink(e);
    case "mutter":
      // 碎碎念与 reading_mutter 统一走悬浮气泡。
      return useAnnounceStore.getState().announce("mutter", e.content);
    case "initiate_chat":
      return useChatStore.getState().addInitiateChat(e);
    case "user_message":
      return useChatStore.getState().addUserMessage(e);
    case "annotation_feedback":
      return useChatStore.getState().addAnnotationFeedback(e);
    case "emotion_update":
      // 情绪帧不包含精力等运行状态，重新读取完整快照。
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
    case "activity_progress":
    case "activity_interrupted":
    case "task_updated":
      useActivityStore.getState().refresh();
      return;
    case "activity_end": {
      const scope = captureScope();
      // 完成后主动冒一句：refresh 重拉快照后，按 activity_id 找到刚完成的活动，
      // 有产出就 announce("activity", …)（activityAnnouncement 见 lib/activityResult）。
      void useActivityStore.getState().refresh().then(() => {
        if (!isScopeCurrent(scope)) return;
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
      // 读书提问并进对话（08 §2.3）：并进对话转录，不再走 readerStore 气泡流。
      return useChatStore.getState().addReadingTurn(e);
    case "reading_association":
      // 联想不进对话（对话框只放对话+提问）：后端仍产生，前端静默丢弃。
      return;
    case "reading_progress":
      return useReaderStore.getState().applyProgressEvent(e);
    case "reflection_done": {
      void useMemoryStore.getState().refresh();
      // 内在文档与长期欲望可能变化；内部反思摘要只保存在反思记录中。
      void useDesireStore.getState().refresh();
      void useInnerLifeStore.getState().refreshState();
      return;
    }
  }
}
