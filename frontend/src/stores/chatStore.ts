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
  isReplying: boolean;
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

// 占位：SSE 分发 / 发消息 / isReplying 生命周期在 02-stores 实现阶段填充
export const useChatStore = create<ChatState>(() => ({
  messages: [],
  isReplying: false,
  sendError: null,
  addUserMessage: () => {},
  addSpeak: () => {},
  addAsk: () => {},
  addThink: () => {},
  addMutter: () => {},
  addInitiateChat: () => {},
  sendMessage: async () => {},
  reset: () => {},
}));
