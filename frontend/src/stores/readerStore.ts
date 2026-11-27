import readingPolicy from "../../../nyx/settings/reading.json";
import { captureScope, isScopeCurrent, scopedSetter } from "../api/characterScope";
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

// 阅读系统唯一 store：书架/进度/段落/追赶循环。
// 笔记同属本 store；概览见 docs/facts/reading-system-facts.md。

export type NyxStatus = "idle" | "reading" | "waiting"; // 派生态，不落 store

export const WINDOW_SIZE = readingPolicy.reader.window_size;
const MIN_CATCHUP_SEC = readingPolicy.frontend.readerStore.MIN_CATCHUP_SEC; // 段落未加载 / 过短时的保底节奏
const MAX_CATCHUP_SEC = readingPolicy.frontend.readerStore.MAX_CATCHUP_SEC; // 单段追赶耗时上界
const MIN_READING_SPEED = readingPolicy.reader.min_speed; // 字/秒，后端校验下界 [10, 200]（reading-system spec）
const CATCHUP_REFRESH_FRACTION = readingPolicy.frontend.readerStore.CATCHUP_REFRESH_FRACTION;

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
  refreshProgress: () => Promise<void>;
  syncPosition: (next: number) => Promise<void>;
  jumpToPosition: (next: number) => Promise<void>;
  setReadingSpeed: (speed: number) => Promise<void>;
  startCatchup: () => void;
  stopCatchup: () => void;
  advanceNyx: () => void;
  applyProgressEvent: (e: ReadingProgressEvent, followUser?: boolean) => void;
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

// 追赶 timer 放 module-level（不进 store state，同 chatStore 的 replyTimer 约定）。
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
  const scope = captureScope();
  const previous = impulseQueues.get(key);
  const run = () => isScopeCurrent(scope)
    ? evaluateImpulse(bookId, paragraphIndex, lastParagraphIndex).then(() => undefined)
    : Promise.resolve();
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
    user_read?: boolean;
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
      const scope = captureScope();
      const commit = scopedSetter(set);
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
      if (!isScopeCurrent(scope)) return;
      if (isCurrent() && get().bookId === bookId) {
        commit({
          nyxPosition: allowNyxRewind
            ? result.nyx_position
            : Math.max(get().nyxPosition, result.nyx_position),
          progressRevision: result.revision,
          readCount: result.read_count,
        });
      }
    } catch {
      if (!isScopeCurrent(scope)) return;
      try {
        const fresh = await getProgress(bookId);
      if (!isScopeCurrent(scope)) return;
        if (!isCurrent() || get().bookId !== bookId) return;
        const retryNyxPosition = allowNyxRewind
          ? payload.nyx_position
          : Math.max(desiredNyxPosition, fresh.nyx_position, get().nyxPosition);
        if (isCurrent() && get().bookId === bookId) {
          commit({
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
      if (!isScopeCurrent(scope)) return;
        if (isCurrent() && get().bookId === bookId) {
          commit({
            nyxPosition: allowNyxRewind
              ? retry.nyx_position
              : Math.max(get().nyxPosition, retry.nyx_position),
            progressRevision: retry.revision,
            readCount: retry.read_count,
          });
        }
      } catch {
      if (!isScopeCurrent(scope)) return;
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

// 尽量把当前位置放在窗口中央，书首和书末仍保留完整的可读窗口。
export function computeWindow(
  userPosition: number,
  totalParagraphs: number,
): { from: number; to: number } {
  const total = Math.max(1, totalParagraphs);
  const center = userPosition - Math.floor(WINDOW_SIZE / 2);
  const from = Math.max(1, Math.min(center, total - WINDOW_SIZE + 1));
  const to = Math.min(total, from + WINDOW_SIZE - 1);
  return { from, to };
}

function needsWindowRefresh(position: number, from: number, total: number): boolean {
  const forward = Math.floor(WINDOW_SIZE * CATCHUP_REFRESH_FRACTION);
  const backward = WINDOW_SIZE - forward;
  return from === 0 || position < from || position >= from + WINDOW_SIZE
    || (from > 1 && position < from + backward)
    || (from + WINDOW_SIZE <= total && position >= from + forward);
}

export const useReaderStore = create<ReaderState>((set, get) => {
  let sessionGeneration = 0;
  let windowRequest: { session: number; from: number; promise: Promise<void> } | undefined;
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
  ): Promise<void> => {
    const commit = scopedSetter(set);
    if (!isCurrent(session, bookId)) return;
    if (total <= 0) {
      commit({ paragraphs: [], windowFrom: 0 });
      return;
    }
    if (!needsWindowRefresh(userPosition, get().windowFrom, total)) return;
    const { from, to } = computeWindow(userPosition, total);
    if (get().windowFrom === from) return;
    if (windowRequest?.session === session &&
      (windowRequest.from === from || !needsWindowRefresh(userPosition, windowRequest.from, total))) {
      return windowRequest.promise;
    }
    const promise = getBookParagraphs(bookId, from, to).then((paragraphs) => {
      const current = get();
      if (windowRequest?.promise === promise && isCurrent(session, bookId)
        && current.userPosition >= from && current.userPosition <= to) {
        commit({ paragraphs, windowFrom: from });
      }
    }).finally(() => {
      if (windowRequest?.promise === promise) windowRequest = undefined;
    });
    windowRequest = { session, from, promise };
    await promise;
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
    readingSpeed: readingPolicy.reader.default_speed,
    readCount: 0,
    progressRevision: 0,
    notes: [],
    notesError: null,
    bookmarks: [],
    bookmarksError: null,

    loadBooks: async () => {
      const scope = captureScope();
      const commit = scopedSetter(set);
      commit({ booksError: null });
      try {
        const books = await getBooks();
      if (!isScopeCurrent(scope)) return;
        commit({ books });
      } catch (err) {
      if (!isScopeCurrent(scope)) return;
        commit({ booksError: err instanceof Error ? err.message : String(err) });
      }
    },

    openBook: async (bookId) => {
      const scope = captureScope();
      const commit = scopedSetter(set);
      const session = ++sessionGeneration;
      get().stopCatchup();
      try {
        let book = get().books.find((b) => b.id === bookId);
        if (book === undefined || book.total_paragraphs <= 0) {
          const books = await getBooks();
      if (!isScopeCurrent(scope)) return;
          if (sessionGeneration !== session) return;
          commit({ books });
          book = books.find((b) => b.id === bookId);
        }
        const total = book?.total_paragraphs ?? 0;
        if (sessionGeneration !== session) return;
        commit({
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
      if (!isScopeCurrent(scope)) return;
        if (!isCurrent(session, bookId)) return;
        const userPosition = progress.user_position;
        const nyxPosition = progress.nyx_position;
        commit({
          userPosition,
          nyxPosition,
          readingSpeed: progress.reading_speed,
          readCount: progress.read_count,
          progressRevision: progress.revision ?? 0,
        });
        await fetchWindow(session, bookId, userPosition, total);
      if (!isScopeCurrent(scope)) return;
        if (isCurrent(session, bookId) && nyxPosition < userPosition) {
          get().startCatchup();
        }
      } catch (err) {
      if (!isScopeCurrent(scope)) return;
        if (sessionGeneration === session) {
          commit({ booksError: err instanceof Error ? err.message : String(err) });
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

    refreshProgress: async () => {
      const scope = captureScope();
      const session = sessionGeneration;
      const { bookId, totalParagraphs } = get();
      if (bookId === null) return;
      try {
        const progress = await getProgress(bookId);
        if (!isScopeCurrent(scope) || !isCurrent(session, bookId)) return;
        const current = get();
        if (progress.revision >= current.progressRevision &&
            !progressQueues.has(session + ":" + bookId)) {
          set({
            userPosition: progress.user_position, nyxPosition: progress.nyx_position,
            readingSpeed: progress.reading_speed, readCount: progress.read_count,
            progressRevision: progress.revision,
          });
          await fetchWindow(session, bookId, progress.user_position, totalParagraphs);
        }
        if (isScopeCurrent(scope) && isCurrent(session, bookId)) get().startCatchup();
      } catch {
        // 断线时保留当前正文。
      }
    },

    syncPosition: async (next) => {
      const scope = captureScope();
      const commit = scopedSetter(set);
      const session = sessionGeneration;
      const { bookId, totalParagraphs, userPosition } = get();
      if (bookId === null || totalParagraphs <= 0) return;
      const clamped = Math.min(totalParagraphs, Math.max(1, next));
      if (clamped === userPosition) {
        if (get().windowFrom > 0) {
          await fetchWindow(session, bookId, clamped, totalParagraphs).catch(() => {});
        }
        return;
      }
      commit({ userPosition: clamped });
      const { nyxPosition, readingSpeed } = get();
      if (!isCurrent(session, bookId)) return;
      // 进度持久化失败时由下一次阅读位置更新重新提交。
      const progress = enqueueProgress(session, bookId, {
        user_position: clamped,
        nyx_position: nyxPosition,
        reading_speed: readingSpeed,
        ...(clamped > userPosition ? { user_read: true } : {}),
      }, get, set, () => isCurrent(session, bookId));
      const window = get().windowFrom > 0
        ? fetchWindow(session, bookId, clamped, totalParagraphs).catch(() => {})
        : undefined;
      await progress;
      if (!isScopeCurrent(scope)) return;
      if (!isCurrent(session, bookId)) return;
      // 向前滑过多段时逐段评估；向后滑动仅保存位置。
      if (clamped > userPosition) {
        const impulseTasks: Promise<void>[] = [];
        for (let i = userPosition + 1; i <= clamped; i += 1) {
          impulseTasks.push(enqueueImpulse(session, bookId, i, i - 1));
        }
        await Promise.allSettled(impulseTasks);
      }
      await window;
      if (!isScopeCurrent(scope)) return;
      if (isCurrent(session, bookId)) get().startCatchup();
    },

    jumpToPosition: async (next) => {
      const scope = captureScope();
      const commit = scopedSetter(set);
      const session = sessionGeneration;
      const { bookId, totalParagraphs, userPosition } = get();
      if (bookId === null || totalParagraphs <= 0) return;
      const clamped = Math.min(totalParagraphs, Math.max(1, next));
      if (clamped === userPosition) {
        await fetchWindow(session, bookId, clamped, totalParagraphs).catch(() => {});
        return;
      }
      commit({ userPosition: clamped });
      const { nyxPosition, readingSpeed } = get();
      if (!isCurrent(session, bookId)) return;
      await enqueueProgress(session, bookId, {
        user_position: clamped,
        nyx_position: nyxPosition,
        reading_speed: readingSpeed,
      }, get, set, () => isCurrent(session, bookId));
      if (!isScopeCurrent(scope)) return;
      if (isCurrent(session, bookId) && get().userPosition === clamped) {
        await fetchWindow(session, bookId, clamped, totalParagraphs).catch(() => {});
      }
      if (!isScopeCurrent(scope)) return;
      if (isCurrent(session, bookId)) get().startCatchup();
    },

    setReadingSpeed: async (speed) => {
      const scope = captureScope();
      const commit = scopedSetter(set);
      const session = sessionGeneration;
      const { bookId, userPosition, nyxPosition } = get();
      if (bookId === null) return;
      commit({ readingSpeed: speed });
      await enqueueProgress(session, bookId, {
        user_position: userPosition,
        nyx_position: nyxPosition,
        reading_speed: speed,
      }, get, set, () => isCurrent(session, bookId));
      if (!isScopeCurrent(scope)) return;
    },

    startCatchup: () => {
      clearCatchupTimer();
      const { bookId, nyxPosition, userPosition, paragraphs, readingSpeed } = get();
      if (document.hidden || bookId === null || nyxPosition >= userPosition) return;
      const para = paragraphs.find((p) => p.index === nyxPosition);
      const len = para?.text.length ?? 0;
      const durMs = len > 0 ? catchupDurationMs(len, readingSpeed) : MIN_CATCHUP_SEC * 1000;
      const scope = captureScope();
      const session = sessionGeneration;
      catchupTimer = setTimeout(() => {
        if (isScopeCurrent(scope) && isCurrent(session, bookId)) get().advanceNyx();
      }, durMs);
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
        // 落库，否则重载后读到陈旧落后值会重追、重放 BOOK_FINISHED——阅读幂等靠进程内
        // _finished_books，重启即丢 → read_count 重复 ++ 且误触 reflect。
        const readingSpeed = get().readingSpeed;
      void enqueueProgress(session, bookId, {
        user_position: userPosition,
        nyx_position: next,
        reading_speed: readingSpeed,
        }, get, set, () => isCurrent(session, bookId));
      }
    },

    applyProgressEvent: (e, followUser = false) => {
      const current = get();
      if (
        current.bookId !== e.book_id ||
        e.revision <= current.progressRevision ||
        (!followUser && e.nyx_position < current.nyxPosition)
      ) {
        return;
      }
      const session = sessionGeneration;
      const followPosition = followUser && !progressQueues.has(session + ":" + e.book_id);
      const position = followPosition ? e.user_position : current.userPosition;
      set({
        ...(followPosition ? { userPosition: position, readingSpeed: e.reading_speed } : {}),
        nyxPosition: e.nyx_position,
        readCount: e.read_count,
        progressRevision: e.revision,
      });
      if (followPosition) {
        void fetchWindow(session, e.book_id, position, current.totalParagraphs).catch(() => {});
      }
      if (e.nyx_position < position) {
        get().startCatchup();
      } else {
        get().stopCatchup();
      }
    },

    reread: async () => {
      const scope = captureScope();
      const commit = scopedSetter(set);
      const session = sessionGeneration;
      const { bookId, totalParagraphs, readingSpeed } = get();
      if (bookId === null) return;
      get().stopCatchup();
      commit({ userPosition: 1, nyxPosition: 1 });
      // read_count 后端不碰（保持 >=1）；只复位进度。
      await enqueueProgress(session, bookId, {
        user_position: 1,
        nyx_position: 1,
        reading_speed: readingSpeed,
      }, get, set, () => isCurrent(session, bookId), true);
      if (!isScopeCurrent(scope)) return;
      if (!isCurrent(session, bookId)) return;
      if (get().userPosition === 1) {
        await fetchWindow(session, bookId, 1, totalParagraphs).catch(() => {});
      }
      if (!isScopeCurrent(scope)) return;
    },

    loadNotes: async () => {
      const scope = captureScope();
      const { bookId } = get();
      if (bookId === null) return;
      const session = sessionGeneration;
      // 笔记读写共用串行队列；后续写操作不能作废它依赖的读取快照。
      const request = ++notesRequestRevision;
      const key = session + ":" + bookId;
      await enqueueOperation(noteQueues, key, async () => {
      const scope = captureScope();
      const commit = scopedSetter(set);
        if (!isCurrent(session, bookId)) return;
        commit({ notesError: null });
        try {
          const notes = await getNotes(bookId);
      if (!isScopeCurrent(scope)) return;
          if (
            isCurrent(session, bookId) &&
            request === notesRequestRevision
          ) {
            commit({ notes });
          }
        } catch (err) {
      if (!isScopeCurrent(scope)) return;
          if (
            isCurrent(session, bookId) &&
            request === notesRequestRevision
          ) {
            commit({
              notesError: err instanceof Error ? err.message : String(err),
            });
          }
        }
      });
      if (!isScopeCurrent(scope)) return;
    },

    // POST 返回裸 UserNote（7 键无 annotations），归一成 UserNoteWithAnnotations 再 unshift。
    addNote: async (p) => {
      const session = sessionGeneration;
      const bookId = get().bookId;
      if (bookId === null || p.book_id !== bookId) return false;
      return enqueueOperation(noteQueues, session + ":" + bookId, async () => {
      const scope = captureScope();
      const commit = scopedSetter(set);
        if (!isCurrent(session, bookId)) return false;
        try {
          const note = await createUserNote(p);
      if (!isScopeCurrent(scope)) return false;
          if (!isCurrent(session, bookId)) return false;
          commit({ notes: [{ ...note, annotations: [] }, ...get().notes] });
          return true;
        } catch (err) {
      if (!isScopeCurrent(scope)) return false;
          if (isCurrent(session, bookId)) {
            commit({ notesError: err instanceof Error ? err.message : String(err) });
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
      return enqueueOperation(noteQueues, session + ":" + bookId, async () => {
      const scope = captureScope();
      const commit = scopedSetter(set);
        if (!isCurrent(session, bookId)) return false;
        try {
          const note = await updateUserNote(id, content, get().notes.find((n) => n.id === id)?.revision);
      if (!isScopeCurrent(scope)) return false;
          if (!isCurrent(session, bookId)) return false;
          commit({
            notes: get().notes.map((n) =>
              n.id === id ? { ...note, annotations: n.annotations } : n,
            ),
          });
          return true;
        } catch (err) {
      if (!isScopeCurrent(scope)) return false;
          if (isCurrent(session, bookId)) {
            commit({ notesError: err instanceof Error ? err.message : String(err) });
          }
          return false;
        }
      });
    },

    deleteNote: async (id) => {
      const session = sessionGeneration;
      const bookId = get().bookId;
      if (bookId === null) return false;
      return enqueueOperation(noteQueues, session + ":" + bookId, async () => {
      const scope = captureScope();
      const commit = scopedSetter(set);
        if (!isCurrent(session, bookId)) return false;
        try {
          await deleteUserNote(id);
      if (!isScopeCurrent(scope)) return false;
          if (!isCurrent(session, bookId)) return false;
          commit({ notes: get().notes.filter((n) => n.id !== id) });
          return true;
        } catch (err) {
      if (!isScopeCurrent(scope)) return false;
          if (isCurrent(session, bookId)) {
            commit({ notesError: err instanceof Error ? err.message : String(err) });
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
      return enqueueOperation(noteQueues, session + ":" + bookId, async () => {
      const scope = captureScope();
      const commit = scopedSetter(set);
        if (!isCurrent(session, bookId)) return false;
        try {
          const ann = await showNoteToNyx(noteId);
      if (!isScopeCurrent(scope)) return false;
          if (!isCurrent(session, bookId)) return false;
          if (ann !== null) {
            commit({
              notes: get().notes.map((n) =>
                n.id === noteId
                  ? { ...n, annotations: [...n.annotations, ann] }
                  : n,
              ),
            });
          }
          return true;
        } catch (err) {
      if (!isScopeCurrent(scope)) return false;
          if (isCurrent(session, bookId)) {
            commit({ notesError: err instanceof Error ? err.message : String(err) });
          }
          return false;
        }
      });
    },

    loadBookmarks: async () => {
      const scope = captureScope();
      const { bookId } = get();
      if (bookId === null) return;
      const session = sessionGeneration;
      const request = ++bookmarksRequestRevision;
      const key = session + ":" + bookId;
      await enqueueOperation(bookmarkQueues, key, async () => {
      const scope = captureScope();
      const commit = scopedSetter(set);
        if (!isCurrent(session, bookId)) return;
        commit({ bookmarksError: null });
        try {
          const bookmarks = await getBookmarks(bookId);
      if (!isScopeCurrent(scope)) return;
          if (
            isCurrent(session, bookId) &&
            request === bookmarksRequestRevision
          ) {
            commit({ bookmarks });
          }
        } catch (err) {
      if (!isScopeCurrent(scope)) return;
          if (
            isCurrent(session, bookId) &&
            request === bookmarksRequestRevision
          ) {
            commit({
              bookmarksError: err instanceof Error ? err.message : String(err),
            });
          }
        }
      });
      if (!isScopeCurrent(scope)) return;
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
      const scope = captureScope();
      const commit = scopedSetter(set);
          if (!isCurrent(session, bookId)) return;
          const existing = get().bookmarks.find(
            (item) => item.paragraph_id === paragraphId,
          );
          try {
            if (existing !== undefined) {
              await deleteBookmark(existing.id);
      if (!isScopeCurrent(scope)) return;
              if (isCurrent(session, bookId)) {
                commit({
                  bookmarks: get().bookmarks.filter(
                    (item) => item.id !== existing.id,
                  ),
                });
              }
              return;
            }
            const bookmark = await createBookmark(bookId, paragraphId);
      if (!isScopeCurrent(scope)) return;
            if (isCurrent(session, bookId)) {
              commit({
                bookmarks: [...get().bookmarks, bookmark].sort(
                  (a, b) => a.paragraph_index - b.paragraph_index,
                ),
              });
            }
          } catch (err) {
      if (!isScopeCurrent(scope)) return;
            if (isCurrent(session, bookId)) {
              commit({
                bookmarksError: err instanceof Error ? err.message : String(err),
              });
            }
          }
        }
      );
    },
  };
});
