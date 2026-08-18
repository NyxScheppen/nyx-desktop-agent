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

function clearReplyTimer(): void {
  if (replyTimer !== null) {
    clearTimeout(replyTimer);
    replyTimer = null;
  }
}

export const useChatStore = create<ChatState>((set) => {
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

  return {
    messages: [],
    isReplying: false,
    sendError: null,
    addUserMessage: (e) => append(e, "user", "message"),
    addSpeak: (e) => {
      clearReplyTimer(); // 收到回复即取消 60s 超时（02-stores §1）
      append(e, "nyx", "speak");
      set({ isReplying: false });
    },
    addAsk: (e) => {
      clearReplyTimer();
      append(e, "nyx", "ask");
      set({ isReplying: false });
    },
    addThink: (e) => append(e, "nyx", "think"),
    addMutter: (e) => append(e, "nyx", "mutter"),
    addInitiateChat: (e) => append(e, "nyx", "initiate_chat"),
    sendMessage: async (text) => {
      try {
        await postChat(text);
        set({ isReplying: true, sendError: null });
        clearReplyTimer();
        replyTimer = setTimeout(() => {
          replyTimer = null;
          set({ isReplying: false, sendError: "回复超时" });
        }, REPLY_TIMEOUT_MS);
      } catch (err) {
        // postChat throw：isReplying 未置，无需复位（02-stores §1）
        set({ sendError: err instanceof Error ? err.message : String(err) });
      }
    },
    reset: () => set({ messages: [] }),
  };
});
