import { create } from "zustand";
import { getEventsLog, postChat } from "../api/client";
import type {
  BackendEvent,
  QuestionSubtype,
  ReadingQuestionEvent,
  TextEvent,
  TextEventType,
  UserMessageEvent,
} from "../types/api";

export type ChatMessage = {
  id: string; // event_id
  role: "user" | "nyx";
  kind:
    | "message" | "speak" | "ask" | "think" | "initiate_chat"
    | "reading_question";
  content: string;
  correlation_id: string;
  preloaded?: boolean; // 历史回填消息：渲染时不逐字
  // 读书 turn 专属（kind==="reading_question" 才有 subtype/selectedText）
  subtype?: QuestionSubtype;
  selectedText?: string | null;
};

type ChatState = {
  messages: ChatMessage[];
  isReplying: boolean; // 发消息后等待回复中
  sendError: string | null;
  typedIds: Record<string, true>; // 已逐字打完的 think id（speak/ask 等其同 correlation_id 的 think 打完才开打）
  unreadProactive: boolean; // 搭话（initiate_chat）未读：头像红点，用户点徽标/发消息即清
  addUserMessage: (e: UserMessageEvent) => void;
  addSpeak: (e: TextEvent<"speak">) => void;
  addAsk: (e: TextEvent<"ask">) => void;
  addThink: (e: TextEvent<"think">) => void;
  addInitiateChat: (e: TextEvent<"initiate_chat">) => void;
  addReadingTurn: (e: ReadingQuestionEvent) => void;
  clearUnreadProactive: () => void;
  markTyped: (id: string) => void;
  loadHistory: () => Promise<void>; // 挂载时回填 GET /api/events/log 的历史消息（preloaded，不逐字）
  sendMessage: (text: string) => Promise<boolean>; // 成功 true / 失败 false（ChatInput 据其决定是否清空输入框）
  reset: () => void;
};

// 历史回填的事件类型（按 03-chat-panel §4）：只拉文本类，其余（emotion_update 等）不进对话。
const HISTORY_TYPES = [
  "user_message",
  "speak",
  "ask",
  "think",
  "initiate_chat",
  "reading_question",
] as const;
const HISTORY_LIMIT = 5000; // 每类型拉取上限（大上限折中：覆盖长会话、后端不动）

// BackendEvent（event_log）→ ChatMessage：user_message 读 content.message、文本事件读 content.content。
// 字段非 string 则丢弃（与 append 一致的收窄校验，01-sse §4.1）。
function toChatMessage(e: BackendEvent): ChatMessage | null {
  const isUser = e.type === "user_message";
  const isQuestion = e.type === "reading_question";
  const raw = isUser ? e.content.message : e.content.content;
  if (typeof raw !== "string") return null;
  const msg: ChatMessage = {
    id: e.id,
    role: isUser ? "user" : "nyx",
    kind: isUser ? "message" : (e.type as ChatMessage["kind"]),
    content: raw,
    correlation_id: e.correlation_id,
    preloaded: true,
  };
  if (isQuestion) {
    msg.subtype = e.content.subtype as QuestionSubtype;
    msg.selectedText = e.content.selected_text as string | null;
  }
  return msg;
}

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
    typedIds: {},
    unreadProactive: false,
    addUserMessage: (e) => append(e, "user", "message"),
    addSpeak: (e) => finishReply(e),
    addAsk: (e) => finishReply(e),
    addThink: (e) => append(e, "nyx", "think"),
    addInitiateChat: (e) => {
      append(e, "nyx", "initiate_chat");
      set({ unreadProactive: true });
    },
    // 读书提问并进对话（08 §2.2）：correlation_id = book_id（后端用 book_id 当 correlation_id），
    // 不过滤当前书（永久聊天消息，关书后仍留转录）；文本字段非 string 丢弃（复用 append 收窄）。
    addReadingTurn: (e) => {
      if (typeof e.content !== "string") return;
      set((s) => ({
        messages: [
          ...s.messages,
          {
            id: e.event_id,
            role: "nyx",
            kind: "reading_question",
            content: e.content,
            correlation_id: e.book_id,
            subtype: e.subtype,
            selectedText: e.selected_text,
          },
        ],
      }));
    },
    clearUnreadProactive: () => set({ unreadProactive: false }),
    markTyped: (id) => set((s) => ({ typedIds: { ...s.typedIds, [id]: true } })),
    loadHistory: async () => {
      // 每类型并行拉取，合并后按时间升序（旧→新，历史在前）。getEventsLog 失败即整组放弃
      // （与 snapshot store 一致：loadHistory 是 best-effort，不阻塞实时 SSE）。
      try {
        const byType = await Promise.all(
          HISTORY_TYPES.map((t) => getEventsLog({ event_type: t, limit: HISTORY_LIMIT })),
        );
        const merged = byType.flat().sort((a, b) => a.timestamp - b.timestamp);
        set((s) => {
          const existing = new Set(s.messages.map((m) => m.id));
          const fresh: ChatMessage[] = [];
          const thinkIds: Record<string, true> = {};
          for (const e of merged) {
            if (existing.has(e.id)) continue;
            const msg = toChatMessage(e);
            if (msg === null) continue;
            existing.add(msg.id);
            fresh.push(msg);
            if (msg.kind === "think") thinkIds[msg.id] = true;
          }
          if (fresh.length === 0) return {};
          // 前置到现有消息前；历史 think 视为已打完（不阻塞实时 speak/ask）
          return {
            messages: [...fresh, ...s.messages],
            typedIds: { ...s.typedIds, ...thinkIds },
          };
        });
      } catch (err) {
        console.error("加载聊天历史失败", err);
      }
    },
    sendMessage: async (text) => {
      // 串行锁要同步上：get() 同步读 store（非 React 订阅），在 await postChat 之前置 isReplying=true。
      // 否则网络往返窗口内 isReplying 仍 false，双击/连击可并发第二次发送、覆盖 pendingId。
      if (get().isReplying) return false;
      set({ isReplying: true, sendError: null, unreadProactive: false });
      try {
        const { event_id } = await postChat(text);
        pendingId = event_id; // 回复帧 correlation_id 与此匹配（后端 user_message 沿它溯源）
        clearReplyTimer();
        replyTimer = setTimeout(() => {
          replyTimer = null;
          // 不清 pendingId：迟到回复仍需能匹配并清 sendError
          set({ isReplying: false, sendError: "回复超时" });
        }, REPLY_TIMEOUT_MS);
        return true;
      } catch (err) {
        // 锁已提前上，失败须复位 isReplying（原先 isReplying 在 await 后才置、catch 无需复位）
        set({ isReplying: false, sendError: err instanceof Error ? err.message : String(err) });
        return false;
      }
    },
    reset: () => {
      clearReplyTimer(); // 新会话：取消残留 timer + 复位 isReplying/sendError（防假超时）
      pendingId = null;
      set({ messages: [], isReplying: false, sendError: null, typedIds: {}, unreadProactive: false });
    },
  };
});
