import { create } from "zustand";
import type { SseEvent } from "../types/api";

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
  addUserMessage: (e: SseEvent) => void;
  addSpeak: (e: SseEvent) => void;
  addAsk: (e: SseEvent) => void;
  addThink: (e: SseEvent) => void;
  addMutter: (e: SseEvent) => void;
  addInitiateChat: (e: SseEvent) => void;
  sendMessage: (text: string) => Promise<void>;
  reset: () => void;
};

export const useChatStore = create<ChatState>((set) => {
  // content 收窄校验后才入消息（01-sse §4.1：不用裸 as，类型错 → 丢弃不崩）
  const append = (
    e: SseEvent,
    role: ChatMessage["role"],
    kind: ChatMessage["kind"],
  ) => {
    if (typeof e.content !== "string") {
      console.error(`SSE ${e.event} 帧 content 非 string，丢弃`, e);
      return;
    }
    const msg: ChatMessage = {
      id: e.event_id,
      role,
      kind,
      content: e.content,
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
