import { create } from "zustand";
import { captureScope, isScopeCurrent } from "../api/characterScope";
import {
  enterStudy,
  getStudy,
  getStudyNote,
  leaveStudy,
  saveStudyNote,
  sendStudyInput,
  studyConnection,
  retryStudy,
  retryStudyTurn,
} from "../api/client";
import type {
  StudyInput,
  StudyNote,
  StudySnapshot,
} from "../types/readingStudy";
import { useAnnounceStore } from "./announceStore";

type StudyState = {
  snapshot: StudySnapshot | null;
  connection: string | null;
  entering: boolean;
  error: string | null;
  pendingInput: StudyInput | null;
  sending: boolean;
  note: StudyNote | null;
  noteDraft: string;
  noteError: string | null;
  saving: boolean;
  open: (bookId: string) => Promise<void>;
  refresh: () => Promise<void>;
  heartbeat: () => Promise<void>;
  detach: () => void;
  reset: () => void;
  send: (input: StudyInput) => Promise<boolean>;
  retryTurn: (id: string) => Promise<void>;
  leave: (keepDraft?: boolean) => Promise<boolean>;
  retry: () => Promise<void>;
  editNote: (text: string) => void;
  saveNote: () => Promise<boolean>;
  reloadNote: () => Promise<void>;
};
let generation = 0;
let saveTask: Promise<boolean> | null = null;
function draftKey(bookId: string): string {
  return `nyx-reading-note:${captureScope()?.id ?? "standalone"}:${bookId}`;
}

export const useStudyStore = create<StudyState>((set, get) => ({
  snapshot: null,
  connection: null,
  entering: false,
  error: null,
  pendingInput: null,
  sending: false,
  note: null,
  noteDraft: "",
  noteError: null,
  saving: false,
  open: async (bookId) => {
    const scope = captureScope();
    const epoch = ++generation;
    const connection = crypto.randomUUID();
    useAnnounceStore.setState({ items: [] });
    set({ entering: true, error: null, connection });
    try {
      const snapshot = await enterStudy(bookId, connection);
      if (!isScopeCurrent(scope) || epoch !== generation) return;
      const note = await getStudyNote(snapshot.book.id);
      if (!isScopeCurrent(scope) || epoch !== generation) return;
      const draft = localStorage.getItem(draftKey(snapshot.book.id));
      set({
        snapshot,
        note,
        noteDraft: draft ?? note.content,
        noteError: null,
      });
    } catch (error) {
      if (isScopeCurrent(scope) && epoch === generation)
        set({ error: String(error) });
    } finally {
      if (isScopeCurrent(scope) && epoch === generation)
        set({ entering: false });
    }
  },
  refresh: async () => {
    const scope = captureScope();
    const epoch = generation;
    const current = get().snapshot;
    if (!current) return;
    try {
      const snapshot = await getStudy(current.book.id, current.session.id);
      if (!isScopeCurrent(scope) || epoch !== generation) return;
      if (snapshot.revision >= (get().snapshot?.revision ?? 0))
        set({ snapshot });
    } catch (error) {
      if (isScopeCurrent(scope) && epoch === generation)
        set({ error: String(error) });
    }
  },
  heartbeat: async () => {
    const scope = captureScope();
    const epoch = generation;
    const { snapshot, connection } = get();
    if (!snapshot || !connection || snapshot.session.ended_at !== null) return;
    try {
      await studyConnection(
        snapshot.book.id,
        snapshot.session.id,
        connection,
        "heartbeat",
      );
    } catch (error) {
      if (isScopeCurrent(scope) && epoch === generation)
        set({ error: String(error) });
    }
  },
  detach: () => {
    generation += 1;
    const { snapshot, connection } = get();
    if (snapshot && connection)
      void studyConnection(
        snapshot.book.id,
        snapshot.session.id,
        connection,
        "detach",
      ).catch(() => {});
    set({ connection: null, entering: false });
  },
  reset: () => {
    generation += 1;
    saveTask = null;
    set(useStudyStore.getInitialState());
  },
  send: async (input) => {
    const scope = captureScope();
    const epoch = generation;
    const { snapshot, connection } = get();
    if (!snapshot || !connection || get().sending) return false;
    set({ sending: true, pendingInput: input, error: null });
    try {
      await sendStudyInput(
        snapshot.book.id,
        snapshot.session.id,
        connection,
        input,
      );
      if (!isScopeCurrent(scope) || epoch !== generation) return false;
      set({ pendingInput: null });
      await get().refresh();
      return true;
    } catch (error) {
      if (isScopeCurrent(scope) && epoch === generation)
        set({ error: String(error) });
      return false;
    } finally {
      if (isScopeCurrent(scope) && epoch === generation)
        set({ sending: false });
    }
  },
  retryTurn: async (id) => {
    const scope = captureScope();
    const epoch = generation;
    const { snapshot, connection } = get();
    if (!snapshot || !connection) return;
    try {
      await retryStudyTurn(
        snapshot.book.id,
        snapshot.session.id,
        id,
        connection,
      );
    } catch (error) {
      if (isScopeCurrent(scope) && epoch === generation)
        set({ error: String(error) });
    }
    if (isScopeCurrent(scope) && epoch === generation) await get().refresh();
  },
  leave: async (keepDraft = false) => {
    const scope = captureScope();
    const epoch = generation;
    const current = get().snapshot;
    if (!current) return true;
    if (!keepDraft && !(await get().saveNote())) return false;
    try {
      await leaveStudy(current.book.id, current.session.id);
      if (!isScopeCurrent(scope) || epoch !== generation) return false;
      get().detach();
      get().reset();
      return true;
    } catch (error) {
      if (isScopeCurrent(scope) && epoch === generation)
        set({ error: String(error) });
      return false;
    }
  },
  retry: async () => {
    const scope = captureScope();
    const epoch = generation;
    const current = get().snapshot;
    if (!current) return;
    try {
      await retryStudy(current.book.id);
    } catch (error) {
      if (isScopeCurrent(scope) && epoch === generation)
        set({ error: String(error) });
    }
    if (isScopeCurrent(scope) && epoch === generation) await get().refresh();
  },
  editNote: (text) => {
    set({ noteDraft: text });
    const bookId = get().snapshot?.book.id;
    if (bookId) localStorage.setItem(draftKey(bookId), text);
  },
  saveNote: async () => {
    if (saveTask) return saveTask;
    const scope = captureScope();
    const epoch = generation;
    const bookId = get().snapshot?.book.id;
    if (!bookId || !get().note) return true;
    const task = (async () => {
      set({ saving: true });
      try {
        while (isScopeCurrent(scope) && epoch === generation) {
          const { note, noteDraft } = get();
          if (!note || note.content === noteDraft) return true;
          const saved = await saveStudyNote(bookId, noteDraft, note.revision);
          if (!isScopeCurrent(scope) || epoch !== generation) return false;
          set({ note: saved, noteError: null });
          if (get().noteDraft === saved.content)
            localStorage.removeItem(draftKey(bookId));
        }
        return false;
      } catch (error) {
        if (isScopeCurrent(scope) && epoch === generation)
          set({
            noteError: error instanceof Error ? error.message : String(error),
          });
        return false;
      } finally {
        if (isScopeCurrent(scope) && epoch === generation)
          set({ saving: false });
      }
    })();
    saveTask = task;
    try {
      return await task;
    } finally {
      if (saveTask === task) saveTask = null;
    }
  },
  reloadNote: async () => {
    const scope = captureScope();
    const epoch = generation;
    const bookId = get().snapshot?.book.id;
    if (!bookId) return;
    try {
      const note = await getStudyNote(bookId);
      if (!isScopeCurrent(scope) || epoch !== generation) return;
      localStorage.removeItem(draftKey(bookId));
      set({ note, noteDraft: note.content, noteError: null });
    } catch (error) {
      if (isScopeCurrent(scope) && epoch === generation)
        set({ noteError: String(error) });
    }
  },
}));
