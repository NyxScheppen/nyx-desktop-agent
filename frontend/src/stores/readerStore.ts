import { create } from "zustand";
import {
  checkChapterBoundary,
  createUserNote,
  deleteUserNote,
  evaluateImpulse,
  getBookParagraphs,
  getBooks,
  getNotes,
  getProgress,
  putProgress,
  showNoteToNyx,
  updateUserNote,
} from "../api/client";
import type {
  BookListItem,
  Paragraph,
  ReadingAssociationEvent,
  ReadingMutterEvent,
  ReadingQuestionEvent,
  UserNoteWithAnnotations,
} from "../types/api";

// 阅读系统唯一 store（06-reading-panel §3）：书架/进度/段落/追赶循环。
// 冲动气泡 + 笔记见 07-reading-events，同属本 store（不拆 impulseStore/noteStore）。

export type NyxStatus = "idle" | "reading" | "waiting"; // 派生态，不落 store

export const WINDOW_SIZE = 50; // 每窗段数（§5 决策）
const MIN_CATCHUP_SEC = 1; // 段落未加载 / 过短时的保底节奏
const MAX_CATCHUP_SEC = 30; // 单段追赶耗时上界
const MIN_READING_SPEED = 10; // 字/秒，后端校验下界 [10, 200]（20-reading-progress）
const CATCHUP_REFRESH_FRACTION = 0.8; // 窗口 80% 边界触发重拉
const _BUBBLE_CAP = 20; // 气泡上限：溢出丢最旧（07 §2）
export const GAP_PX = 12; // 段间距（px），对齐 CSS .reader-text__pages 的 gap: 0.75rem（08 §5.1）

// 冲动气泡三态（07 §2）：对应三个 SSE 事件，字段各落对。
export type ReadingBubbleKind = "mutter" | "question" | "association";
export type ReadingBubble = {
  id: string; // 事件 event_id
  kind: ReadingBubbleKind;
  bookId: string;
  paragraphIndex: number;
  content: string; // mutter/question 用 content；association 用 snippet
  subtype?: "question_knowledge" | "question_personal" | "question_reflective" | "quote_question";
  selectedText?: string | null;
  memoryId?: string;
};

type ReaderState = {
  books: BookListItem[]; // 书架快照
  booksError: string | null;
  bookId: string | null; // 当前打开的书（null = 未开书）
  totalParagraphs: number; // 当前书总段数（openBook 从 books 取；0 = 未开书）
  paragraphs: Paragraph[]; // 当前窗口段落
  windowFrom: number; // 当前窗口起始 index（1-based）
  userPosition: number; // 用户读到第几段（1-based）
  nyxPosition: number; // Nyx 读到第几段（1-based）
  readingSpeed: number; // 字符/秒
  readCount: number; // 读完几遍（0=未读完，>=1 可重读）
  impulseBubbles: ReadingBubble[]; // 冲动气泡（只收当前书，cap 20）
  notes: UserNoteWithAnnotations[]; // 用户笔记（含批注）
  notesError: string | null;
  loadBooks: () => Promise<void>;
  openBook: (bookId: string) => Promise<void>;
  closeBook: () => void;
  syncPosition: (next: number) => Promise<void>;
  setReadingSpeed: (speed: number) => Promise<void>;
  startCatchup: () => void;
  stopCatchup: () => void;
  advanceNyx: () => void;
  reread: () => Promise<void>;
  addReadingBubble: (e: ReadingMutterEvent | ReadingQuestionEvent | ReadingAssociationEvent) => void;
  loadNotes: () => Promise<void>;
  addNote: (p: { book_id: string; paragraph_id?: string | null; content: string; selected_text?: string | null }) => Promise<void>;
  updateNote: (id: string, content: string) => Promise<void>;
  deleteNote: (id: string) => Promise<void>;
  showToNyx: (noteId: string) => Promise<void>;
};

// 追赶 timer 放 module-level（不进 store state，同 chatStore 的 replyTimer 约定，02-stores §1）。
let catchupTimer: ReturnType<typeof setTimeout> | null = null;

function clearCatchupTimer(): void {
  if (catchupTimer !== null) {
    clearTimeout(catchupTimer);
    catchupTimer = null;
  }
}

// 派生态：idle（未开书）/ reading（Nyx 落后）/ waiting（Nyx 追上）。纯函数可测。
export function nyxStatusOf(
  bookId: string | null,
  nyxPosition: number,
  userPosition: number,
): NyxStatus {
  if (bookId === null) return "idle";
  return nyxPosition < userPosition ? "reading" : "waiting";
}

// 追赶节奏：clamp(段落字数 / readingSpeed, 1, 30) 秒。纯函数可测。
export function catchupDurationMs(textLength: number, readingSpeed: number): number {
  // speed 兜底用真正的最小速度，别复用 MIN_CATCHUP_SEC（=1 是「秒」不是「字/秒」）。
  const speed = Math.max(MIN_READING_SPEED, readingSpeed);
  const sec = Math.min(MAX_CATCHUP_SEC, Math.max(MIN_CATCHUP_SEC, textLength / speed));
  return sec * 1000;
}

// 窗口计算：clamp 到 [1, total]（后端 20 对越界 from/to 返回 422，前端必须先 clamp）。
// centered=false：窗口从 userPosition 起（当前段在窗口顶）；true：以 userPosition 为中心。
export function computeWindow(
  userPosition: number,
  totalParagraphs: number,
  centered: boolean,
): { from: number; to: number } {
  const total = Math.max(1, totalParagraphs);
  let from = centered ? userPosition - Math.floor(WINDOW_SIZE / 2) : userPosition;
  from = Math.max(1, Math.min(from, total));
  const to = Math.min(total, from + WINDOW_SIZE - 1);
  return { from, to };
}

// 真分页（08 §5.1）：对当前窗口段落贪心填满。measureHeight(i) 返回第 i 段（全局 1-based）
// 渲染高度 + 段间距；累计将溢出 viewportHeight 则封页、下一段开新页。空/<=0 返回 []。
export function paginate(
  paragraphs: Paragraph[],
  measureHeight: (index: number) => number,
  viewportHeight: number,
): number[][] {
  if (paragraphs.length === 0 || viewportHeight <= 0) return [];
  const pages: number[][] = [];
  let current: number[] = [];
  let used = 0;
  for (const p of paragraphs) {
    const h = measureHeight(p.index);
    if (current.length > 0 && used + h > viewportHeight) {
      pages.push(current);
      current = [];
      used = 0;
    }
    current.push(p.index);
    used += h;
  }
  if (current.length > 0) pages.push(current);
  return pages;
}

// 是否需要重拉窗口：userPosition 越过窗口 80% 边界或跌出窗口起点。
function needsWindowRefresh(userPosition: number, windowFrom: number): boolean {
  const threshold = windowFrom + Math.floor(WINDOW_SIZE * CATCHUP_REFRESH_FRACTION);
  return userPosition < windowFrom || userPosition >= threshold;
}

export const useReaderStore = create<ReaderState>((set, get) => {
  const fetchWindow = async (
    bookId: string,
    userPosition: number,
    total: number,
    centered: boolean,
  ): Promise<void> => {
    if (total <= 0) {
      set({ paragraphs: [], windowFrom: 0 });
      return;
    }
    const { from, to } = computeWindow(userPosition, total, centered);
    const paragraphs = await getBookParagraphs(bookId, from, to);
    set({ paragraphs, windowFrom: from });
  };

  return {
    books: [],
    booksError: null,
    bookId: null,
    totalParagraphs: 0,
    paragraphs: [],
    windowFrom: 0,
    userPosition: 1,
    nyxPosition: 1,
    readingSpeed: 50,
    readCount: 0,
    impulseBubbles: [],
    notes: [],
    notesError: null,

    loadBooks: async () => {
      set({ booksError: null });
      try {
        const books = await getBooks();
        set({ books });
      } catch (err) {
        set({ booksError: err instanceof Error ? err.message : String(err) });
      }
    },

    openBook: async (bookId) => {
      // totalParagraphs 唯一现成来源是书架列表项（后端 GET /api/progress 不回 total）。
      const total = get().books.find((b) => b.id === bookId)?.total_paragraphs ?? 0;
      set({ bookId, totalParagraphs: total, booksError: null });
      try {
        const progress = await getProgress(bookId);
        const userPosition = progress.user_position;
        const nyxPosition = progress.nyx_position;
        set({
          userPosition,
          nyxPosition,
          readingSpeed: progress.reading_speed,
          readCount: progress.read_count,
        });
        await fetchWindow(bookId, userPosition, total, false);
        if (nyxPosition < userPosition) get().startCatchup();
      } catch (err) {
        set({ booksError: err instanceof Error ? err.message : String(err) });
      }
    },

    closeBook: () => {
      get().stopCatchup();
      set({
        bookId: null,
        totalParagraphs: 0,
        paragraphs: [],
        windowFrom: 0,
        userPosition: 1,
        nyxPosition: 1,
        readCount: 0,
        impulseBubbles: [],
        notes: [],
        notesError: null,
      });
    },

    syncPosition: async (next) => {
      const { bookId, totalParagraphs, userPosition } = get();
      if (bookId === null || totalParagraphs <= 0) return;
      const clamped = Math.min(totalParagraphs, Math.max(1, next));
      if (clamped === userPosition) return;
      set({ userPosition: clamped });
      const { nyxPosition, readingSpeed } = get();
      // 进度持久化后写：fire-and-forget，失败静默、下次翻页重写覆盖。
      void putProgress(bookId, {
        user_position: clamped,
        nyx_position: nyxPosition,
        reading_speed: readingSpeed,
      }).catch(() => {});
      // 前翻逐段补发冲动（整屏翻一次跨 N 段，逐段 evaluate 保住每段都有机会触发；
      // 后翻不评估，双保险；正文后端自取，不传 text）。
      if (clamped > userPosition) {
        for (let i = userPosition + 1; i <= clamped; i += 1) {
          void evaluateImpulse(bookId, i, i - 1).catch(() => {});
        }
      }
      if (needsWindowRefresh(clamped, get().windowFrom)) {
        await fetchWindow(bookId, clamped, totalParagraphs, false);
      }
      get().startCatchup();
    },

    setReadingSpeed: async (speed) => {
      const { bookId, userPosition, nyxPosition } = get();
      if (bookId === null) return;
      set({ readingSpeed: speed });
      void putProgress(bookId, {
        user_position: userPosition,
        nyx_position: nyxPosition,
        reading_speed: speed,
      }).catch(() => {});
    },

    startCatchup: () => {
      clearCatchupTimer();
      const { bookId, nyxPosition, userPosition, paragraphs, readingSpeed } = get();
      if (bookId === null || nyxPosition >= userPosition) return;
      const para = paragraphs.find((p) => p.index === nyxPosition);
      const len = para?.text.length ?? 0;
      const durMs = len > 0 ? catchupDurationMs(len, readingSpeed) : MIN_CATCHUP_SEC * 1000;
      catchupTimer = setTimeout(() => get().advanceNyx(), durMs);
    },

    stopCatchup: () => {
      clearCatchupTimer();
    },

    advanceNyx: () => {
      clearCatchupTimer();
      const { bookId, nyxPosition, userPosition } = get();
      if (bookId === null) return;
      const next = Math.min(nyxPosition + 1, userPosition); // 不超车
      set({ nyxPosition: next });
      // 章末/整本检测 fire-and-forget（07）；前端不渲染结果（章末整合落 memory）。
      void checkChapterBoundary(bookId, next).catch(() => {});
      if (next < userPosition) {
        get().startCatchup();
      } else {
        // 追赶收尾（追上 userPosition，无「下一次 putProgress」）：把最新 nyx_position
        // 落库，否则重载后读到陈旧落后值会重追、重放 BOOK_FINISHED——22 幂等靠进程内
        // _finished_books，重启即丢 → read_count 重复 ++ 且误触 reflect。
        const readingSpeed = get().readingSpeed;
        void putProgress(bookId, {
          user_position: userPosition,
          nyx_position: next,
          reading_speed: readingSpeed,
        }).catch(() => {});
      }
    },

    reread: async () => {
      const { bookId, totalParagraphs, readingSpeed } = get();
      if (bookId === null) return;
      get().stopCatchup();
      set({ userPosition: 1, nyxPosition: 1 });
      // read_count 后端不碰（保持 >=1）；只复位进度。
      void putProgress(bookId, {
        user_position: 1,
        nyx_position: 1,
        reading_speed: readingSpeed,
      }).catch(() => {});
      await fetchWindow(bookId, 1, totalParagraphs, false);
    },

    // 只收当前书事件（非当前书丢弃，避免书架切换后旧书气泡串场）；append + cap 丢最旧。
    addReadingBubble: (e) => {
      if (e.book_id !== get().bookId) return;
      let bubble: ReadingBubble;
      switch (e.event) {
        case "reading_mutter":
          bubble = {
            id: e.event_id,
            kind: "mutter",
            bookId: e.book_id,
            paragraphIndex: e.paragraph_index,
            content: e.content,
          };
          break;
        case "reading_question":
          bubble = {
            id: e.event_id,
            kind: "question",
            bookId: e.book_id,
            paragraphIndex: e.paragraph_index,
            content: e.content,
            subtype: e.subtype,
            selectedText: e.selected_text,
          };
          break;
        case "reading_association":
          bubble = {
            id: e.event_id,
            kind: "association",
            bookId: e.book_id,
            paragraphIndex: e.paragraph_index,
            content: e.snippet,
            memoryId: e.memory_id,
          };
          break;
      }
      const next = [...get().impulseBubbles, bubble];
      set({ impulseBubbles: next.slice(-_BUBBLE_CAP) });
    },

    loadNotes: async () => {
      const { bookId } = get();
      if (bookId === null) return;
      set({ notesError: null });
      try {
        const notes = await getNotes(bookId);
        set({ notes });
      } catch (err) {
        set({ notesError: err instanceof Error ? err.message : String(err) });
      }
    },

    // POST 返回裸 UserNote（7 键无 annotations），归一成 UserNoteWithAnnotations 再 unshift。
    addNote: async (p) => {
      try {
        const note = await createUserNote(p);
        set({ notes: [{ ...note, annotations: [] }, ...get().notes] });
      } catch (err) {
        set({ notesError: err instanceof Error ? err.message : String(err) });
      }
    },

    // PUT 覆盖 7 键、保留原有 annotations（后端不回批注，整表重拉代价高）。
    updateNote: async (id, content) => {
      try {
        const note = await updateUserNote(id, content);
        set({
          notes: get().notes.map((n) =>
            n.id === id ? { ...note, annotations: n.annotations } : n,
          ),
        });
      } catch (err) {
        set({ notesError: err instanceof Error ? err.message : String(err) });
      }
    },

    deleteNote: async (id) => {
      try {
        await deleteUserNote(id);
        set({ notes: get().notes.filter((n) => n.id !== id) });
      } catch (err) {
        set({ notesError: err instanceof Error ? err.message : String(err) });
      }
    },

    // 成功后 annotations append 完整 Annotation（不整表重拉）；LLM 空回 null 不 append。
    showToNyx: async (noteId) => {
      try {
        const ann = await showNoteToNyx(noteId);
        if (ann === null) return;
        set({
          notes: get().notes.map((n) =>
            n.id === noteId ? { ...n, annotations: [...n.annotations, ann] } : n,
          ),
        });
      } catch (err) {
        set({ notesError: err instanceof Error ? err.message : String(err) });
      }
    },
  };
});
