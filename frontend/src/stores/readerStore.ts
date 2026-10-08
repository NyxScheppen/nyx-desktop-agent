import { create } from "zustand";
import {
  checkChapterBoundary,
  createBookmark,
  createUserNote,
  deleteBookmark,
  deleteUserNote,
  evaluateImpulse,
  getBookParagraphs,
  getBooks,
  getBookmarks,
  getNotes,
  getProgress,
  putProgress,
  showNoteToNyx,
  updateUserNote,
} from "../api/client";
import type {
  BookListItem,
  Bookmark,
  Paragraph,
  ReadingProgressEvent,
  UserNoteWithAnnotations,
} from "../types/api";

// 阅读系统唯一 store（06-reading-panel §3）：书架/进度/段落/追赶循环。
// 笔记见 docs/frontend/07-reading-events.md，同属本 store（不拆 impulseStore/noteStore）。

export type NyxStatus = "idle" | "reading" | "waiting"; // 派生态，不落 store

export const WINDOW_SIZE = 50; // 每窗段数（§5 决策）
const MIN_CATCHUP_SEC = 1; // 段落未加载 / 过短时的保底节奏
const MAX_CATCHUP_SEC = 30; // 单段追赶耗时上界
const MIN_READING_SPEED = 10; // 字/秒，后端校验下界 [10, 200]（reading-system spec）
const CATCHUP_REFRESH_FRACTION = 0.8; // 窗口 80% 边界触发重拉
export const GAP_PX = 12; // 段间距（px），对齐 CSS .reader-text__pages 的 gap: 0.75rem（08 §5.1）

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
  progressRevision: number; // 后端 CAS 版本
  notes: UserNoteWithAnnotations[]; // 用户笔记（含批注）
  notesError: string | null;
  bookmarks: Bookmark[];
  bookmarksError: string | null;
  loadBooks: () => Promise<void>;
  openBook: (bookId: string) => Promise<void>;
  closeBook: () => void;
  syncPosition: (next: number) => Promise<void>;
  jumpToPosition: (next: number) => Promise<void>;
  setReadingSpeed: (speed: number) => Promise<void>;
  startCatchup: () => void;
  stopCatchup: () => void;
  advanceNyx: () => void;
  applyProgressEvent: (e: ReadingProgressEvent) => void;
  reread: () => Promise<void>;
  loadNotes: () => Promise<void>;
  addNote: (p: {
    book_id: string;
    paragraph_id?: string | null;
    content: string;
    selected_text?: string | null;
    selection_start?: number | null;
    selection_end?: number | null;
  }) => Promise<boolean>;
  updateNote: (id: string, content: string) => Promise<boolean>;
  deleteNote: (id: string) => Promise<boolean>;
  showToNyx: (noteId: string) => Promise<boolean>;
  loadBookmarks: () => Promise<void>;
  toggleBookmark: (paragraphId: string) => Promise<void>;
};

// 追赶 timer 放 module-level（不进 store state，同 chatStore 的 replyTimer 约定，02-stores §1）。
let catchupTimer: ReturnType<typeof setTimeout> | null = null;
const progressQueues = new Map<string, Promise<void>>();
const impulseQueues = new Map<string, Promise<void>>();

function clearCatchupTimer(): void {
  if (catchupTimer !== null) {
    clearTimeout(catchupTimer);
    catchupTimer = null;
  }
}

function enqueueImpulse(
  session: number,
  bookId: string,
  paragraphIndex: number,
  lastParagraphIndex: number,
): Promise<void> {
  const key = session + ":" + bookId;
  const previous = impulseQueues.get(key);
  const run = () =>
    evaluateImpulse(bookId, paragraphIndex, lastParagraphIndex).then(
      () => undefined,
    );
  const next =
    previous === undefined ? run() : previous.catch(() => {}).then(run);
  const tracked = next.finally(() => {
    if (impulseQueues.get(key) === tracked) impulseQueues.delete(key);
  });
  impulseQueues.set(key, tracked);
  return tracked;
}

function enqueueProgress(
  session: number,
  bookId: string,
  payload: {
    user_position: number;
    nyx_position: number;
    reading_speed: number;
  },
  get: () => ReaderState,
  set: (state: Partial<ReaderState>) => void,
  isCurrent: () => boolean,
  allowNyxRewind = false,
): Promise<void> {
  const key = session + ":" + bookId;
  const previous = progressQueues.get(key);
  const run = async (): Promise<void> => {
    if (!isCurrent() || get().bookId !== bookId) return;
    const current = get();
    const desiredNyxPosition = allowNyxRewind
      ? payload.nyx_position
      : Math.max(payload.nyx_position, current.nyxPosition);
    try {
      const result = await putProgress(bookId, {
        ...payload,
        nyx_position: desiredNyxPosition,
        expected_revision: current.progressRevision,
      });
      if (isCurrent() && get().bookId === bookId) {
        set({
          nyxPosition: allowNyxRewind
            ? result.nyx_position
            : Math.max(get().nyxPosition, result.nyx_position),
          progressRevision: result.revision,
          readCount: result.read_count,
        });
      }
    } catch {
      try {
        const fresh = await getProgress(bookId);
        const retryNyxPosition = allowNyxRewind
          ? payload.nyx_position
          : Math.max(desiredNyxPosition, fresh.nyx_position, get().nyxPosition);
        if (isCurrent() && get().bookId === bookId) {
          set({
            nyxPosition: allowNyxRewind
              ? get().nyxPosition
              : Math.max(get().nyxPosition, fresh.nyx_position),
            progressRevision: fresh.revision,
            readCount: fresh.read_count,
          });
        }
        const retry = await putProgress(bookId, {
          ...payload,
          nyx_position: retryNyxPosition,
          expected_revision: fresh.revision,
        });
        if (isCurrent() && get().bookId === bookId) {
          set({
            nyxPosition: allowNyxRewind
              ? retry.nyx_position
              : Math.max(get().nyxPosition, retry.nyx_position),
            progressRevision: retry.revision,
            readCount: retry.read_count,
          });
        }
      } catch {
        // 下一次用户动作会再次尝试写入。
      }
    }
  };
  const next = previous === undefined ? run() : previous.catch(() => {}).then(run);
  const tracked = next.finally(() => {
    if (progressQueues.get(key) === tracked) progressQueues.delete(key);
  });
  progressQueues.set(key, tracked);
  return tracked;
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

// 窗口计算：clamp 到 [1, total]（12-reading-system 后端对越界 from/to 返回 422，前端必须先 clamp）。
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
  let sessionGeneration = 0;
  let notesRequestRevision = 0;
  let bookmarksRequestRevision = 0;
  const noteQueues = new Map<string, Promise<unknown>>();
  const bookmarkQueues = new Map<string, Promise<unknown>>();

  const isCurrent = (session: number, bookId: string): boolean =>
    sessionGeneration === session && get().bookId === bookId;

  const enqueueOperation = <T>(
    queues: Map<string, Promise<unknown>>,
    key: string,
    operation: () => Promise<T>,
  ): Promise<T> => {
    const previous = queues.get(key);
    const next =
      previous === undefined
        ? operation()
        : previous.catch(() => undefined).then(operation);
    const tracked = next.finally(() => {
      if (queues.get(key) === tracked) queues.delete(key);
    });
    queues.set(key, tracked);
    return tracked;
  };

  const fetchWindow = async (
    session: number,
    bookId: string,
    userPosition: number,
    total: number,
    centered: boolean,
  ): Promise<void> => {
    if (!isCurrent(session, bookId)) return;
    if (total <= 0) {
      set({ paragraphs: [], windowFrom: 0 });
      return;
    }
    const { from, to } = computeWindow(userPosition, total, centered);
    const paragraphs = await getBookParagraphs(bookId, from, to);
    if (isCurrent(session, bookId)) set({ paragraphs, windowFrom: from });
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
    progressRevision: 0,
    notes: [],
    notesError: null,
    bookmarks: [],
    bookmarksError: null,

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
      const session = ++sessionGeneration;
      get().stopCatchup();
      try {
        let book = get().books.find((b) => b.id === bookId);
        if (book === undefined || book.total_paragraphs <= 0) {
          const books = await getBooks();
          if (sessionGeneration !== session) return;
          set({ books });
          book = books.find((b) => b.id === bookId);
        }
        const total = book?.total_paragraphs ?? 0;
        if (sessionGeneration !== session) return;
        set({
          bookId,
          totalParagraphs: total,
          booksError: null,
          progressRevision: 0,
          notes: [],
          notesError: null,
          bookmarks: [],
          bookmarksError: null,
        });
        const progress = await getProgress(bookId);
        if (!isCurrent(session, bookId)) return;
        const userPosition = progress.user_position;
        const nyxPosition = progress.nyx_position;
        set({
          userPosition,
          nyxPosition,
          readingSpeed: progress.reading_speed,
          readCount: progress.read_count,
          progressRevision: progress.revision ?? 0,
        });
        await fetchWindow(session, bookId, userPosition, total, false);
        if (isCurrent(session, bookId) && nyxPosition < userPosition) {
          get().startCatchup();
        }
      } catch (err) {
        if (sessionGeneration === session) {
          set({ booksError: err instanceof Error ? err.message : String(err) });
        }
      }
    },

    closeBook: () => {
      sessionGeneration += 1;
      get().stopCatchup();
      set({
        bookId: null,
        totalParagraphs: 0,
        paragraphs: [],
        windowFrom: 0,
        userPosition: 1,
        nyxPosition: 1,
        readCount: 0,
        progressRevision: 0,
        notes: [],
        notesError: null,
        bookmarks: [],
        bookmarksError: null,
      });
    },

    syncPosition: async (next) => {
      const session = sessionGeneration;
      const { bookId, totalParagraphs, userPosition } = get();
      if (bookId === null || totalParagraphs <= 0) return;
      const clamped = Math.min(totalParagraphs, Math.max(1, next));
      if (clamped === userPosition) return;
      set({ userPosition: clamped });
      const { nyxPosition, readingSpeed } = get();
      if (!isCurrent(session, bookId)) return;
      // 进度持久化后写：fire-and-forget，失败静默、下次翻页重写覆盖。
      await enqueueProgress(session, bookId, {
        user_position: clamped,
        nyx_position: nyxPosition,
        reading_speed: readingSpeed,
      }, get, set, () => isCurrent(session, bookId));
      if (!isCurrent(session, bookId)) return;
      // 前翻逐段补发冲动（整屏翻一次跨 N 段，逐段 evaluate 保住每段都有机会触发；
      // 后翻不评估，双保险；正文后端自取，不传 text）。
      if (clamped > userPosition) {
        const impulseTasks: Promise<void>[] = [];
        for (let i = userPosition + 1; i <= clamped; i += 1) {
          impulseTasks.push(enqueueImpulse(session, bookId, i, i - 1));
        }
        await Promise.all(impulseTasks);
      }
      if (!isCurrent(session, bookId)) return;
      if (needsWindowRefresh(clamped, get().windowFrom)) {
        await fetchWindow(session, bookId, clamped, totalParagraphs, false);
      }
      if (isCurrent(session, bookId)) get().startCatchup();
    },

    jumpToPosition: async (next) => {
      const session = sessionGeneration;
      const { bookId, totalParagraphs, userPosition } = get();
      if (bookId === null || totalParagraphs <= 0) return;
      const clamped = Math.min(totalParagraphs, Math.max(1, next));
      if (clamped === userPosition) return;
      set({ userPosition: clamped });
      const { nyxPosition, readingSpeed } = get();
      if (!isCurrent(session, bookId)) return;
      await enqueueProgress(session, bookId, {
        user_position: clamped,
        nyx_position: nyxPosition,
        reading_speed: readingSpeed,
      }, get, set, () => isCurrent(session, bookId));
      if (!isCurrent(session, bookId)) return;
      if (needsWindowRefresh(clamped, get().windowFrom)) {
        await fetchWindow(session, bookId, clamped, totalParagraphs, false);
      }
      if (isCurrent(session, bookId)) get().startCatchup();
    },

    setReadingSpeed: async (speed) => {
      const session = sessionGeneration;
      const { bookId, userPosition, nyxPosition } = get();
      if (bookId === null) return;
      set({ readingSpeed: speed });
      await enqueueProgress(session, bookId, {
        user_position: userPosition,
        nyx_position: nyxPosition,
        reading_speed: speed,
      }, get, set, () => isCurrent(session, bookId));
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
      const session = sessionGeneration;
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
        // 落库，否则重载后读到陈旧落后值会重追、重放 BOOK_FINISHED——12-reading-system 幂等靠进程内
        // _finished_books，重启即丢 → read_count 重复 ++ 且误触 reflect。
        const readingSpeed = get().readingSpeed;
      void enqueueProgress(session, bookId, {
        user_position: userPosition,
        nyx_position: next,
        reading_speed: readingSpeed,
        }, get, set, () => isCurrent(session, bookId));
      }
    },

    applyProgressEvent: (e) => {
      const current = get();
      if (
        current.bookId !== e.book_id ||
        e.revision <= current.progressRevision ||
        e.nyx_position < current.nyxPosition
      ) {
        return;
      }
      set({
        nyxPosition: e.nyx_position,
        readCount: e.read_count,
        progressRevision: e.revision,
      });
      if (e.nyx_position < current.userPosition) {
        get().startCatchup();
      } else {
        get().stopCatchup();
      }
    },

    reread: async () => {
      const session = sessionGeneration;
      const { bookId, totalParagraphs, readingSpeed } = get();
      if (bookId === null) return;
      get().stopCatchup();
      set({ userPosition: 1, nyxPosition: 1 });
      // read_count 后端不碰（保持 >=1）；只复位进度。
      await enqueueProgress(session, bookId, {
        user_position: 1,
        nyx_position: 1,
        reading_speed: readingSpeed,
      }, get, set, () => isCurrent(session, bookId), true);
      if (!isCurrent(session, bookId)) return;
      await fetchWindow(session, bookId, 1, totalParagraphs, false);
    },

    loadNotes: async () => {
      const { bookId } = get();
      if (bookId === null) return;
      const session = sessionGeneration;
      const request = ++notesRequestRevision;
      const key = session + ":" + bookId;
      await enqueueOperation(noteQueues, key, async () => {
        if (!isCurrent(session, bookId)) return;
        set({ notesError: null });
        try {
          const notes = await getNotes(bookId);
          if (
            isCurrent(session, bookId) &&
            request === notesRequestRevision
          ) {
            set({ notes });
          }
        } catch (err) {
          if (
            isCurrent(session, bookId) &&
            request === notesRequestRevision
          ) {
            set({
              notesError: err instanceof Error ? err.message : String(err),
            });
          }
        }
      });
    },

    // POST 返回裸 UserNote（7 键无 annotations），归一成 UserNoteWithAnnotations 再 unshift。
    addNote: async (p) => {
      const session = sessionGeneration;
      const bookId = get().bookId;
      if (bookId === null || p.book_id !== bookId) return false;
      notesRequestRevision += 1;
      return enqueueOperation(noteQueues, session + ":" + bookId, async () => {
        if (!isCurrent(session, bookId)) return false;
        try {
          const note = await createUserNote(p);
          if (!isCurrent(session, bookId)) return false;
          set({ notes: [{ ...note, annotations: [] }, ...get().notes] });
          return true;
        } catch (err) {
          if (isCurrent(session, bookId)) {
            set({ notesError: err instanceof Error ? err.message : String(err) });
          }
          return false;
        }
      });
    },

    // PUT 覆盖 7 键、保留原有 annotations（后端不回批注，整表重拉代价高）。
    updateNote: async (id, content) => {
      const session = sessionGeneration;
      const bookId = get().bookId;
      if (bookId === null) return false;
      notesRequestRevision += 1;
      return enqueueOperation(noteQueues, session + ":" + bookId, async () => {
        if (!isCurrent(session, bookId)) return false;
        try {
          const note = await updateUserNote(id, content);
          if (!isCurrent(session, bookId)) return false;
          set({
            notes: get().notes.map((n) =>
              n.id === id ? { ...note, annotations: n.annotations } : n,
            ),
          });
          return true;
        } catch (err) {
          if (isCurrent(session, bookId)) {
            set({ notesError: err instanceof Error ? err.message : String(err) });
          }
          return false;
        }
      });
    },

    deleteNote: async (id) => {
      const session = sessionGeneration;
      const bookId = get().bookId;
      if (bookId === null) return false;
      notesRequestRevision += 1;
      return enqueueOperation(noteQueues, session + ":" + bookId, async () => {
        if (!isCurrent(session, bookId)) return false;
        try {
          await deleteUserNote(id);
          if (!isCurrent(session, bookId)) return false;
          set({ notes: get().notes.filter((n) => n.id !== id) });
          return true;
        } catch (err) {
          if (isCurrent(session, bookId)) {
            set({ notesError: err instanceof Error ? err.message : String(err) });
          }
          return false;
        }
      });
    },

    // 成功后 annotations append 完整 Annotation（不整表重拉）；LLM 空回 null 不 append。
    showToNyx: async (noteId) => {
      const session = sessionGeneration;
      const bookId = get().bookId;
      if (bookId === null) return false;
      notesRequestRevision += 1;
      return enqueueOperation(noteQueues, session + ":" + bookId, async () => {
        if (!isCurrent(session, bookId)) return false;
        try {
          const ann = await showNoteToNyx(noteId);
          if (!isCurrent(session, bookId)) return false;
          if (ann !== null) {
            set({
              notes: get().notes.map((n) =>
                n.id === noteId
                  ? { ...n, annotations: [...n.annotations, ann] }
                  : n,
              ),
            });
          }
          return true;
        } catch (err) {
          if (isCurrent(session, bookId)) {
            set({ notesError: err instanceof Error ? err.message : String(err) });
          }
          return false;
        }
      });
    },

    loadBookmarks: async () => {
      const { bookId } = get();
      if (bookId === null) return;
      const session = sessionGeneration;
      const request = ++bookmarksRequestRevision;
      const key = session + ":" + bookId;
      await enqueueOperation(bookmarkQueues, key, async () => {
        if (!isCurrent(session, bookId)) return;
        set({ bookmarksError: null });
        try {
          const bookmarks = await getBookmarks(bookId);
          if (
            isCurrent(session, bookId) &&
            request === bookmarksRequestRevision
          ) {
            set({ bookmarks });
          }
        } catch (err) {
          if (
            isCurrent(session, bookId) &&
            request === bookmarksRequestRevision
          ) {
            set({
              bookmarksError: err instanceof Error ? err.message : String(err),
            });
          }
        }
      });
    },

    toggleBookmark: async (paragraphId) => {
      const session = sessionGeneration;
      const { bookId } = get();
      if (bookId === null) return;
      bookmarksRequestRevision += 1;
      return enqueueOperation(
        bookmarkQueues,
        session + ":" + bookId,
        async () => {
          if (!isCurrent(session, bookId)) return;
          const existing = get().bookmarks.find(
            (item) => item.paragraph_id === paragraphId,
          );
          try {
            if (existing !== undefined) {
              await deleteBookmark(existing.id);
              if (isCurrent(session, bookId)) {
                set({
                  bookmarks: get().bookmarks.filter(
                    (item) => item.id !== existing.id,
                  ),
                });
              }
              return;
            }
            const bookmark = await createBookmark(bookId, paragraphId);
            if (isCurrent(session, bookId)) {
              set({
                bookmarks: [...get().bookmarks, bookmark].sort(
                  (a, b) => a.paragraph_index - b.paragraph_index,
                ),
              });
            }
          } catch (err) {
            if (isCurrent(session, bookId)) {
              set({
                bookmarksError: err instanceof Error ? err.message : String(err),
              });
            }
          }
        }
      );
    },
  };
});
