import { create } from "zustand";
import { postChat } from "../api/client";
import type { TextEvent, TextEventType, UserMessageEvent } from "../types/api";

export type ChatMessage = {
  id: string; // event_id
  role: "user" | "nyx";
  kind: "message" | "speak" | "ask" | "think" | "mutter" | "initiate_chat";
  content: string;
  correlation_id: string;
};

type ChatState = {
  messages: ChatMessage[];
  isReplying: boolean; // 发消息后等待回复中
  sendError: string | null;
  addUserMessage: (e: UserMessageEvent) => void;
  addSpeak: (e: TextEvent<"speak">) => void;
  addAsk: (e: TextEvent<"ask">) => void;
  addThink: (e: TextEvent<"think">) => void;
  addMutter: (e: TextEvent<"mutter">) => void;
  addInitiateChat: (e: TextEvent<"initiate_chat">) => void;
  sendMessage: (text: string) => Promise<void>;
  reset: () => void;
};

// 回复超时兜底（02-stores §1）：60s 未收到 speak/ask 视为超时。
// timer 引用放 module-level，不进 store state（store 状态须可序列化）。
const REPLY_TIMEOUT_MS = 60_000;
let replyTimer: ReturnType<typeof setTimeout> | null = null;
// 待回复消息的 event_id（= postChat 返回值 = user_message id = 回复帧 correlation_id）。
// 与 replyTimer 一样放 module-level，不进 store state；addSpeak/addAsk 按 correlation_id 匹配后才清 timer。
let pendingId: string | null = null;

function clearReplyTimer(): void {
  if (replyTimer !== null) {
    clearTimeout(replyTimer);
    replyTimer = null;
  }
}

export const useChatStore = create<ChatState>((set, get) => {
  // 文本字段收窄校验后才入消息（01-sse §4.1：不用裸 as，类型错 → 丢弃不崩）。
  // 用户消息读 message、文本事件读 content（键名不同，见 01-sse §1）。
  const append = (
    e: TextEvent<TextEventType> | UserMessageEvent,
    role: ChatMessage["role"],
    kind: ChatMessage["kind"],
  ) => {
    const text = e.event === "user_message" ? e.message : e.content;
    if (typeof text !== "string") {
      console.error(`SSE ${e.event} 帧文本字段非 string，丢弃`, e);
      return;
    }
    const msg: ChatMessage = {
      id: e.event_id,
      role,
      kind,
      content: text,
      correlation_id: e.correlation_id,
    };
    set((s) => ({ messages: [...s.messages, msg] }));
  };

  // speak/ask 是「会结束回复等待」的产出：correlation_id 匹配本次发送（pendingId）才结束等待，
  // 非匹配（搭话/碎碎念）只 append 不动生命周期。kind 取 e.event（"speak"/"ask"）。
  const finishReply = (e: TextEvent<"speak"> | TextEvent<"ask">) => {
    if (e.correlation_id === pendingId) {
      clearReplyTimer();
      pendingId = null;
      set({ isReplying: false, sendError: null });
    }
    append(e, "nyx", e.event);
  };

  return {
    messages: [],
    isReplying: false,
    sendError: null,
    addUserMessage: (e) => append(e, "user", "message"),
    addSpeak: (e) => finishReply(e),
    addAsk: (e) => finishReply(e),
    addThink: (e) => append(e, "nyx", "think"),
    addMutter: (e) => append(e, "nyx", "mutter"),
    addInitiateChat: (e) => append(e, "nyx", "initiate_chat"),
    sendMessage: async (text) => {
      // 串行锁要同步上：get() 同步读 store（非 React 订阅），在 await postChat 之前置 isReplying=true。
      // 否则网络往返窗口内 isReplying 仍 false，双击/连击可并发第二次发送、覆盖 pendingId。
      if (get().isReplying) return;
      set({ isReplying: true, sendError: null });
      try {
        const { event_id } = await postChat(text);
        pendingId = event_id; // 回复帧 correlation_id 与此匹配（后端 user_message 沿它溯源）
        clearReplyTimer();
        replyTimer = setTimeout(() => {
          replyTimer = null;
          // 不清 pendingId：迟到回复仍需能匹配并清 sendError
          set({ isReplying: false, sendError: "回复超时" });
        }, REPLY_TIMEOUT_MS);
      } catch (err) {
        // 锁已提前上，失败须复位 isReplying（原先 isReplying 在 await 后才置、catch 无需复位）
        set({ isReplying: false, sendError: err instanceof Error ? err.message : String(err) });
      }
    },
    reset: () => {
      clearReplyTimer(); // 新会话：取消残留 timer + 复位 isReplying/sendError（防假超时）
      pendingId = null;
      set({ messages: [], isReplying: false, sendError: null });
    },
  };
});
