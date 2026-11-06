import { captureScope, isScopeCurrent, scopedSetter } from "../api/characterScope";
import { create } from "zustand";
import {
  getEventsLog,
  getChatPending,
  getPendingQuestions,
  pauseQuestion,
  postChat,
  resumeQuestion,
} from "../api/client";
import { isValidTimestamp } from "../lib/time";
import { hasValidReplyCoverage, isReplyAnnotation } from "../types/api";
import type {
  AnnotationFeedbackEvent,
  BackendEvent,
  PendingQuestion,
  QuestionStatus,
  QuestionSubtype,
  ReadingQuestionEvent,
  ReplyAnnotation,
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
  annotation?: ReplyAnnotation;
  correlation_id: string;
  timestamp: number;
  preloaded?: boolean; // 历史回填消息：渲染时不逐字
  // 读书 turn 专属（kind==="reading_question" 才有 subtype/selectedText）
  subtype?: QuestionSubtype;
  selectedText?: string | null;
  attempt_id?: string;
  expires_at?: number;
  paused_at?: number | null;
  question_status?: QuestionStatus;
};

type ChatState = {
  messages: ChatMessage[];
  discussionMode: boolean | null;
  annotationDraft: ReplyAnnotation | null;
  annotationPersistence: Record<string, "pending" | "recorded">;
  beginAnnotation: (eventId: string, quote: string) => void;
  updateComment: (text: string) => void;
  cancelAnnotation: () => void;
  sendAnnotation: () => Promise<boolean>;
  addAnnotationFeedback: (event: AnnotationFeedbackEvent) => void;
  isReplying: boolean;
  isSending: boolean;
  generation: number;
  replyNotice: string | null;
  pendingReplies: Record<string, { status: "waiting" | "timed_out" | "dead_letter"; deadline: number; version: number }>;
  pendingTruncated: boolean;
  sendError: string | null;
  typedIds: Record<string, true>; // 已逐字打完的 think id（speak/ask 等其同 correlation_id 的 think 打完才开打）
  seenIds: Record<string, true>; // 本角色本次前端会话中已开始显示的 nyx 文本 id
  unreadProactive: boolean; // 搭话（initiate_chat）未读：头像红点，用户点徽标/发消息即清
  replyTargetId: string | null;
  serverTimeOffset: number;
  addUserMessage: (e: UserMessageEvent) => void;
  addSpeak: (e: TextEvent<"speak">) => void;
  addAsk: (e: TextEvent<"ask">) => void;
  addThink: (e: TextEvent<"think">) => void;
  addInitiateChat: (e: TextEvent<"initiate_chat">) => void;
  addReadingTurn: (e: ReadingQuestionEvent) => void;
  setDiscussionMode: (mode: boolean | null) => void;
  selectQuestion: (attemptId: string | null) => void;
  pauseQuestion: (attemptId: string) => Promise<boolean>;
  resumeQuestion: (attemptId: string) => Promise<boolean>;
  clearUnreadProactive: () => void;
  markTyped: (id: string) => void;
  markSeen: (id: string) => void;
  loadHistory: () => Promise<void>; // 挂载时回填 GET /api/events/log 的历史消息（preloaded，不逐字）
  sendMessage: (text: string) => Promise<boolean>; // 成功才清输入框
  reset: () => void;
};

// 历史回填的事件类型：只拉文本类，其余（emotion_update 等）不进对话。
const HISTORY_TYPES = [
  "user_message",
  "speak",
  "ask",
  "think",
  "initiate_chat",
  "reading_question",
  "annotation_feedback",
] as const;
const HISTORY_LIMIT = 5000; // 每类型拉取上限（大上限折中：覆盖长会话、后端不动）

function compareMessages(a: ChatMessage, b: ChatMessage): number {
  const rank = (kind: ChatMessage["kind"]) => kind === "message" ? 0 : kind === "think" ? 1 : 2;
  return a.timestamp - b.timestamp || rank(a.kind) - rank(b.kind)
    || a.id.localeCompare(b.id);
}

function questionPriority(message: ChatMessage): number {
  return message.kind === "reading_question" ? 2 : message.kind === "ask" ? 1 : 0;
}

function mergeMessages(messages: ChatMessage[]): ChatMessage[] {
  const byId = new Map<string, ChatMessage>();
  for (const message of messages) byId.set(message.id, message);
  const selectedByAttempt = new Map<string, ChatMessage>();
  for (const message of byId.values()) {
    if (message.attempt_id === undefined) continue;
    const previous = selectedByAttempt.get(message.attempt_id);
    if (previous === undefined || questionPriority(message) >= questionPriority(previous)) {
      selectedByAttempt.set(message.attempt_id, message);
    }
  }
  const hiddenIds = new Set<string>();
  for (const [attemptId, selected] of selectedByAttempt) {
    for (const message of byId.values()) {
      if (message.attempt_id === attemptId && message.id !== selected.id) hiddenIds.add(message.id);
    }
  }
  return [...byId.values()]
    .filter((message) => !hiddenIds.has(message.id))
    .sort(compareMessages);
}

function updateQuestion(
  messages: ChatMessage[],
  question: PendingQuestion,
): ChatMessage[] {
  return messages.map((message) =>
    message.attempt_id === question.attempt_id
      ? {
          ...message,
          expires_at: question.expires_at,
          paused_at: question.paused_at,
          question_status: question.status,
        }
      : message,
  );
}

function closeMissingHistoricalQuestions(
  messages: ChatMessage[],
  pendingIds: Set<string>,
): ChatMessage[] {
  return messages.map((message) =>
    message.attempt_id !== undefined &&
      message.question_status === "waiting" &&
      !pendingIds.has(message.attempt_id)
      ? { ...message, question_status: "answered" }
      : message,
  );
}

// BackendEvent（event_log）→ ChatMessage：user_message 读 content.message、文本事件读 content.content。
// 字段非 string 则丢弃（与 append 一致的收窄校验）。
function toChatMessage(e: BackendEvent): ChatMessage | null {
  if (e.type === "annotation_feedback") return null;
  if ((e.type === "speak" || e.type === "ask") && !hasValidReplyCoverage(e.content, e.correlation_id)) return null;
  const isUser = e.type === "user_message";
  const isQuestion = e.type === "reading_question";
  const raw = isUser ? e.content.message : e.content.content;
  if (typeof raw !== "string" || !isValidTimestamp(e.timestamp)) return null;
  const msg: ChatMessage = {
    id: e.id,
    role: isUser ? "user" : "nyx",
    kind: isUser ? "message" : (e.type as ChatMessage["kind"]),
    content: raw,
    correlation_id: e.correlation_id,
    timestamp: e.timestamp,
    preloaded: true,
  };
  if (isUser && isReplyAnnotation(e.content.annotation)) msg.annotation = e.content.annotation;
  if (isQuestion) {
    msg.subtype = e.content.subtype as QuestionSubtype;
    msg.selectedText = e.content.selected_text as string | null;
  }
  if (typeof e.content.attempt_id === "string") {
    msg.attempt_id = e.content.attempt_id;
    msg.question_status = "waiting";
  }
  if (typeof e.content.expires_at === "number") msg.expires_at = e.content.expires_at;
  return msg;
}

const REPLY_TIMEOUT_MS = 60_000;

export const useChatStore = create<ChatState>((set, get) => {
  let generation = 0;
  let pendingVersion = 0;
  let snapshotSequence = 0;
  const timers = new Map<string, ReturnType<typeof setTimeout>>();
  const completed = new Set<string>();
  const knownInputs = new Set<string>();
  const earlyLegacy = new Set<string>();

  const refreshWaiting = () => {
    const items = Object.values(get().pendingReplies);
    set({ isReplying: items.some((item) => item.status === "waiting"),
      replyNotice: items.some((item) => item.status === "dead_letter") ? "消息处理失败"
        : items.some((item) => item.status === "timed_out") ? "回复超时，仍在等待处理"
        : get().pendingTruncated ? "仍有未显示的待回复消息" : null });
  };
  const removePending = (id: string) => {
    clearTimeout(timers.get(id));
    timers.delete(id);
    set((s) => {
      const pendingReplies = { ...s.pendingReplies };
      delete pendingReplies[id];
      return { pendingReplies };
    });
  };
  const addPending = (id: string, dead = false) => {
    knownInputs.add(id);
    if (completed.has(id)) return;
    const old = get().pendingReplies[id];
    const item = { status: dead ? "dead_letter" as const : old?.status ?? "waiting" as const,
      deadline: old?.deadline ?? performance.now() + REPLY_TIMEOUT_MS, version: ++pendingVersion };
    set((s) => ({ pendingReplies: { ...s.pendingReplies, [id]: item } }));
    if (dead) {
      clearTimeout(timers.get(id)); timers.delete(id);
    } else if (!timers.has(id) && item.status === "waiting") {
      const scope = captureScope();
      const version = generation;
      const check = () => {
        if (!isScopeCurrent(scope) || version !== generation) return;
        const current = get().pendingReplies[id];
        if (!current || current.status !== "waiting") return;
        const remaining = current.deadline - performance.now();
        if (remaining > 0) { timers.set(id, setTimeout(check, remaining)); return; }
        timers.delete(id);
        set((s) => ({ pendingReplies: { ...s.pendingReplies,
          [id]: { ...current, status: "timed_out", version: ++pendingVersion } } }));
        refreshWaiting();
      };
      timers.set(id, setTimeout(check, Math.max(0, item.deadline - performance.now())));
    }
    refreshWaiting();
  };
  const coverReply = (e: TextEvent<"speak"> | TextEvent<"ask">) => {
    if (!hasValidReplyCoverage(e, e.correlation_id)) return false;
    const ids = e.reply_to_event_ids ?? (knownInputs.has(e.correlation_id) ? [e.correlation_id] : []);
    if (!e.reply_to_event_ids && ids.length === 0 && get().isSending) earlyLegacy.add(e.correlation_id);
    for (const id of ids) { completed.add(id); removePending(id); }
    refreshWaiting();
    return true;
  };
  // 文本字段收窄校验后才入消息（不用裸 as，类型错 → 丢弃不崩）。
  // 用户消息读 message、文本事件读 content（键名不同）。
  const append = (
    e: TextEvent<TextEventType> | UserMessageEvent,
    role: ChatMessage["role"],
    kind: ChatMessage["kind"],
  ) => {
    const text = e.event === "user_message" ? e.message : e.content;
    if (typeof text !== "string" || !isValidTimestamp(e.timestamp)) {
      console.error(`SSE ${e.event} 帧文本或时间戳非法，丢弃`, e);
      return false;
    }
    const msg: ChatMessage = {
      id: e.event_id,
      role,
      kind,
      content: text,
      correlation_id: e.correlation_id,
      timestamp: e.timestamp,
    };
    if (e.event === "user_message" && e.annotation) msg.annotation = e.annotation;
    if ("attempt_id" in e && typeof e.attempt_id === "string") {
      msg.attempt_id = e.attempt_id;
      msg.question_status = "waiting";
    }
    if ("expires_at" in e && typeof e.expires_at === "number") {
      msg.expires_at = e.expires_at;
    }
    if (get().messages.some((message) => message.id === msg.id)) return false;
    set((s) => ({
      messages: mergeMessages([...s.messages, msg]),
    }));
    return true;
  };

  const finishReply = (e: TextEvent<"speak"> | TextEvent<"ask">) => {
    if (typeof e.content !== "string" || !isValidTimestamp(e.timestamp)) return;
    if (!coverReply(e)) return;
    append(e, "nyx", e.event);
  };

  const send = async (text: string, annotation?: ReplyAnnotation): Promise<boolean> => {
    const scope = captureScope();
    const version = generation;
    const current = () => isScopeCurrent(scope) && version === generation;
    const commit = scopedSetter(set);
    if (get().isSending) return false;
    const candidates = get().messages.filter(
      (message) => message.attempt_id !== undefined && message.question_status === "waiting",
    );
    const hasPending = Object.values(get().pendingReplies).some((item) => item.status !== "dead_letter");
    const selected = get().replyTargetId;
    const discussion = get().discussionMode;
    if (!annotation && !hasPending && candidates.length > 1 && selected === null) {
      commit({ sendError: "有多个待回复的问题，请先选择要回复的那一条" });
      return false;
    }
    const replyTo = annotation ? undefined : selected ?? (
      !hasPending && candidates.length === 1 ? candidates[0].attempt_id : undefined
    );
    if (annotation) {
      const key = JSON.stringify([annotation.reply_event_id, annotation.comment]);
      set((s) => ({ annotationPersistence: { ...s.annotationPersistence,
        [key]: s.annotationPersistence[key] ?? "pending" } }));
    }
    commit({ isSending: true, sendError: null, unreadProactive: false });
    try {
      const { event_id, annotation_persistence } = await postChat(text, replyTo, discussion, annotation);
      if (!current()) return false;
      if (annotation) {
        const key = JSON.stringify([annotation.reply_event_id, annotation.comment]);
        commit((s) => ({
          annotationPersistence: { ...s.annotationPersistence, [key]:
            s.annotationPersistence[key] === "recorded" || annotation_persistence === "recorded"
              ? "recorded" : "pending" },
          annotationDraft: s.annotationDraft === annotation ? null : s.annotationDraft,
        }));
      }
      commit({ isSending: false });
      if (replyTo !== undefined) {
        commit((s) => ({
          messages: s.messages.map((message) =>
            message.attempt_id === replyTo
              ? { ...message, question_status: "claimed" }
              : message,
          ),
        }));
      }
      if (earlyLegacy.delete(event_id)) completed.add(event_id);
      addPending(event_id);
      if (!annotation && get().replyTargetId === selected) commit({ replyTargetId: null });
      earlyLegacy.clear();
      return true;
    } catch (err) {
      if (!current()) return false;
      commit({ isSending: false });
      earlyLegacy.clear();
      commit({ sendError: err instanceof TypeError
        ? `提交结果未知，服务端可能已受理：${err.message}`
        : err instanceof Error ? err.message : String(err) });
      return false;
    }
  };
  return {
    messages: [],
    discussionMode: null,
    annotationDraft: null,
    annotationPersistence: {},
    isReplying: false,
    isSending: false,
    generation: 0,
    replyNotice: null,
    pendingReplies: {},
    pendingTruncated: false,
    sendError: null,
    typedIds: {},
    seenIds: {},
    unreadProactive: false,
    replyTargetId: null,
    serverTimeOffset: 0,
    beginAnnotation: (eventId, quote) => {
      if (!eventId.trim() || !quote.trim() || Array.from(quote).length > 2000) {
        set({ sendError: "回复较长，请选择片段（最多 2000 字符）后批注" });
        return;
      }
      set({ annotationDraft: { reply_event_id: eventId, quote, comment: "" }, sendError: null });
    },
    updateComment: (comment) => set((s) => ({ annotationDraft: s.annotationDraft ? { ...s.annotationDraft, comment } : null })),
    cancelAnnotation: () => set({ annotationDraft: null }),
    addAnnotationFeedback: (event) => {
      const key = JSON.stringify([event.reply_event_id, event.comment]);
      set((s) => ({ annotationPersistence: { ...s.annotationPersistence, [key]: "recorded" } }));
    },
    addUserMessage: (e) => {
      if (typeof e.message !== "string" || !isValidTimestamp(e.timestamp)) return;
      append(e, "user", "message");
      addPending(e.event_id);
    },
    addSpeak: (e) => finishReply(e),
    addAsk: (e) => finishReply(e),
    addThink: (e) => append(e, "nyx", "think"),
    addInitiateChat: (e) => {
      if (append(e, "nyx", "initiate_chat")) set({ unreadProactive: true });
    },
    // 读书提问并进对话（08 §2.2）：保留后端事件的原始 correlation_id，
    // 不过滤当前书（永久聊天消息，关书后仍留转录）；文本字段非 string 丢弃（复用 append 收窄）。
    addReadingTurn: (e) => {
      if (typeof e.content !== "string" || !isValidTimestamp(e.timestamp)) return;
      const message: ChatMessage = {
        id: e.event_id,
        role: "nyx",
        kind: "reading_question",
        content: e.content,
        correlation_id: e.correlation_id,
        timestamp: e.timestamp,
        subtype: e.subtype,
        selectedText: e.selected_text,
      };
      if (e.attempt_id !== undefined) {
        message.attempt_id = e.attempt_id;
        message.question_status = "waiting";
      }
      if (e.expires_at !== undefined) message.expires_at = e.expires_at;
      set((s) => s.messages.some((item) => item.id === e.event_id)
        ? {}
        : { messages: mergeMessages([...s.messages, message]) });
    },
    setDiscussionMode: (mode) => set({ discussionMode: mode }),
    selectQuestion: (attemptId) => set({ replyTargetId: attemptId }),
    pauseQuestion: async (attemptId) => {
      const scope = captureScope();
      const commit = scopedSetter(set);
      try {
        const question = await pauseQuestion(attemptId);
      if (!isScopeCurrent(scope)) return false;
        commit((s) => ({
          serverTimeOffset: question.server_now - Date.now() / 1000,
          messages: updateQuestion(s.messages, question),
        }));
        return true;
      } catch (err) {
      if (!isScopeCurrent(scope)) return false;
        console.error("暂停提问失败", err);
        return false;
      }
    },
    resumeQuestion: async (attemptId) => {
      const scope = captureScope();
      const commit = scopedSetter(set);
      try {
        const question = await resumeQuestion(attemptId);
      if (!isScopeCurrent(scope)) return false;
        commit((s) => ({
          serverTimeOffset: question.server_now - Date.now() / 1000,
          messages: updateQuestion(s.messages, question),
        }));
        return true;
      } catch (err) {
      if (!isScopeCurrent(scope)) return false;
        console.error("恢复提问失败", err);
        return false;
      }
    },
    clearUnreadProactive: () => set({ unreadProactive: false }),
    markTyped: (id) => set((s) => ({ typedIds: { ...s.typedIds, [id]: true } })),
    markSeen: (id) => set((s) => s.seenIds[id]
      ? s
      : { seenIds: { ...s.seenIds, [id]: true } }),
    loadHistory: async () => {
      const scope = captureScope();
      const version = generation;
      const current = () => isScopeCurrent(scope) && version === generation;
      const commit = scopedSetter(set);
      const sequence = ++snapshotSequence;
      // 每类型并行拉取，合并后按时间升序（旧→新，历史在前）。getEventsLog 失败即整组放弃
      // （与 snapshot store 一致：loadHistory 是 best-effort，不阻塞实时 SSE）。
      try {
        const byType = await Promise.all(
          HISTORY_TYPES.map((t) => getEventsLog({ event_type: t, limit: HISTORY_LIMIT })),
        );
        if (!current()) return;
        const merged = byType.flat().sort((a, b) => a.timestamp - b.timestamp);
        for (const e of merged) if (e.type === "user_message") knownInputs.add(e.id);
        for (const e of merged) {
          if (e.type === "speak" || e.type === "ask") {
            const reply = { ...e.content, event: e.type, event_id: e.id,
              correlation_id: e.correlation_id, timestamp: e.timestamp } as TextEvent<"speak"> | TextEvent<"ask">;
            if (typeof reply.content === "string" && isValidTimestamp(reply.timestamp)) coverReply(reply);
          }
        }
        commit((s) => {
          const persistence = { ...s.annotationPersistence };
          for (const event of merged) {
            if (event.type === "annotation_feedback" && isReplyAnnotation(event.content)) {
              persistence[JSON.stringify([event.content.reply_event_id, event.content.comment])] = "recorded";
            }
          }
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
          // 重连可能回填比现有消息更新的事实；合并后统一排序。
          return {
            annotationPersistence: persistence,
            messages: mergeMessages([...s.messages, ...fresh]),
            typedIds: { ...s.typedIds, ...thinkIds },
          };
        });
        try {
          if (sequence !== snapshotSequence) return;
          const baseline = new Map(Object.entries(get().pendingReplies).map(([id, item]) => [id, item.version]));
          const snapshot = await getChatPending();
          if (!current() || sequence !== snapshotSequence) return;
          const present = new Set(snapshot.items.map((item) => item.event_id));
          if (!snapshot.truncated) {
            for (const [id, version] of baseline) {
              if (!present.has(id) && get().pendingReplies[id]?.version === version) removePending(id);
            }
          }
          set({ pendingTruncated: snapshot.truncated });
          for (const item of snapshot.items) addPending(item.event_id, item.status === "dead_letter");
          refreshWaiting();
        } catch (err) {
          if (!current()) return;
          console.error("加载待回复消息失败", err);
        }
        try {
          const pending = await getPendingQuestions();
          if (!current()) return;
          if (!pending || !Array.isArray(pending.questions)) return;
          commit((s) => ({
            serverTimeOffset: pending.server_now - Date.now() / 1000,
            messages: closeMissingHistoricalQuestions(
              pending.questions.reduce(
                (messages, question) => updateQuestion(messages, question),
                s.messages,
              ),
              new Set(pending.questions.map((question) => question.attempt_id)),
            ),
          }));
        } catch (err) {
          if (!current()) return;
          console.error("加载待回答提问失败", err);
        }
      } catch (err) {
        if (!current()) return;
        console.error("加载聊天历史失败", err);
      }
    },
    sendMessage: (text) => send(text),
    sendAnnotation: async () => {
      const annotation = get().annotationDraft;
      if (!isReplyAnnotation(annotation)) {
        set({ sendError: "请填写有效批注，引用和批注各不超过 2000 字符" });
        return false;
      }
      return send(annotation.comment, annotation);
    },
    reset: () => {
      generation += 1;
      for (const timer of timers.values()) clearTimeout(timer);
      timers.clear(); completed.clear(); knownInputs.clear(); earlyLegacy.clear();
      snapshotSequence += 1;
      set({
        messages: [],
        discussionMode: null,
        annotationDraft: null,
        annotationPersistence: {},
        isReplying: false,
        isSending: false,
        generation,
        replyNotice: null,
        pendingReplies: {},
        pendingTruncated: false,
        sendError: null,
        typedIds: {},
        seenIds: {},
        unreadProactive: false,
        replyTargetId: null,
        serverTimeOffset: 0,
      });
    },
  };
});
