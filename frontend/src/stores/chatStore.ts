import { create } from "zustand";
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
  isReplying: boolean; // 发消息后等待回复中（生命周期 02-stores 实现）
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
    addSpeak: (e) => append(e, "nyx", "speak"),
    addAsk: (e) => append(e, "nyx", "ask"),
    addThink: (e) => append(e, "nyx", "think"),
    addMutter: (e) => append(e, "nyx", "mutter"),
    addInitiateChat: (e) => append(e, "nyx", "initiate_chat"),
    // 占位：发消息 + isReplying 生命周期在 02-stores + 05-client 实现
    sendMessage: async () => {},
    reset: () => set({ messages: [] }),
  };
});
