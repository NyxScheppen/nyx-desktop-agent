import { create } from "zustand";
import { changeCharacter, createCharacter, getCharacters, importCharacter, selectCharacter, type CharacterRegistry, type CreateCharacterPayload } from "../api/client";
import { bindScope, captureScope, invalidateScope } from "../api/characterScope";
import { useActivityStore } from "./activityStore";
import { useAnnounceStore } from "./announceStore";
import { useChatStore } from "./chatStore";
import { useDesireStore } from "./desireStore";
import { useEvalStore } from "./evalStore";
import { useInnerLifeStore } from "./innerLifeStore";
import { useMemoryStore } from "./memoryStore";
import { useReaderStore } from "./readerStore";

type CharacterState = {
  registry: CharacterRegistry | null;
  generation: number;
  switching: boolean;
  error: string | null;
  drafts: Record<string, string>;
  initialize: () => Promise<void>;
  refresh: () => Promise<void>;
  select: (id: string) => Promise<void>;
  import: (file: File) => Promise<void>;
  create: (payload: CreateCharacterPayload, images: File[]) => Promise<void>;
  change: (id: string, operation: "unload" | "restore" | "retry-start") => Promise<void>;
  setDraft: (text: string) => void;
};

let switchQueue = Promise.resolve();
const UI_KEY = "nyx.characterUi";
function readUi(): { drafts: Record<string, string>; lastBooks: Record<string, string> } {
  try {
    const saved = JSON.parse(localStorage.getItem(UI_KEY) ?? "{}");
    const strings = (value: unknown): Record<string, string> =>
      value && typeof value === "object" && !Array.isArray(value)
        ? Object.fromEntries(Object.entries(value).filter(([, v]) => typeof v === "string"))
        : {};
    return { drafts: strings(saved?.drafts), lastBooks: strings(saved?.lastBooks) };
  } catch { return { drafts: {}, lastBooks: {} }; }
}
const savedUi = readUi();
const lastBooks = savedUi.lastBooks;
function saveUi(drafts: Record<string, string>): void {
  try { localStorage.setItem(UI_KEY, JSON.stringify({ drafts, lastBooks })); }
  catch { /* Local storage unavailable: retain the in-memory draft. */ }
}

async function restoreBook(): Promise<void> {
  const selected = captureScope();
  const book = selected && lastBooks[selected.id];
  if (!book) return;
  await useReaderStore.getState().loadBooks();
  if (captureScope() === selected && useReaderStore.getState().books.some((b) => b.id === book)) {
    await useReaderStore.getState().openBook(book);
  }
}

function resetDisplays(): void {
  useChatStore.getState().reset();
  useReaderStore.getState().closeBook();
  useReaderStore.setState(useReaderStore.getInitialState());
  useActivityStore.setState(useActivityStore.getInitialState());
  useAnnounceStore.setState(useAnnounceStore.getInitialState());
  useDesireStore.setState(useDesireStore.getInitialState());
  useEvalStore.setState(useEvalStore.getInitialState());
  useInnerLifeStore.setState(useInnerLifeStore.getInitialState());
  useMemoryStore.setState(useMemoryStore.getInitialState());
}

export const useCharacterStore = create<CharacterState>((set, get) => {
  let refreshRevision = 0;
  const adopt = (registry: CharacterRegistry) => {
    const scope = captureScope();
    const changed = scope?.id !== registry.current_id || scope.revision !== registry.selection_revision;
    if (changed) {
      invalidateScope();
      resetDisplays();
      bindScope({ id: registry.current_id, revision: registry.selection_revision });
      set((s) => ({ generation: s.generation + 1 }));
    }
    set({ registry, switching: false });
    return changed;
  };
  return {
    registry: null, generation: 0, switching: false, error: null, drafts: savedUi.drafts,
    initialize: async () => {
      invalidateScope();
      await get().refresh();
    },
    refresh: async () => {
      if (get().switching) return;
      const request = ++refreshRevision;
      try {
        const registry = await getCharacters();
        if (request === refreshRevision && !get().switching) {
          const changed = adopt(registry);
          set({ error: null });
          if (changed && registry.characters.some((r) => r.id === registry.current_id && r.status === "RUNNING")) {
            await restoreBook();
          }
        }
      } catch (error) {
        if (request === refreshRevision) set({ error: String(error) });
      }
    },
    select: (id) => {
      const run = async () => {
        if (get().registry?.current_id === id && captureScope() !== null) return;
        const previous = captureScope();
        const bookId = useReaderStore.getState().bookId;
        if (previous && bookId) lastBooks[previous.id] = bookId;
        refreshRevision += 1;
        invalidateScope();
        set((s) => ({ switching: true, generation: s.generation + 1, error: null }));
        resetDisplays();
        try {
          await selectCharacter(id);
        } catch (error) {
          set({ error: String(error) });
        }
        try {
          adopt(await getCharacters());
          await restoreBook();
        } catch (error) {
          // No trustworthy selection: keep business requests blocked until refresh succeeds.
          set({ switching: false, error: String(error) });
        }
      };
      switchQueue = switchQueue.then(run, run);
      return switchQueue;
    },
    import: async (file) => {
      try { await importCharacter(file); await get().refresh(); }
      catch (error) { set({ error: String(error) }); }
    },
    create: async (payload, images) => {
      try { await createCharacter(payload, images); await get().refresh(); }
      catch (error) { set({ error: String(error) }); throw error; }
    },
    change: async (id, operation) => {
      try {
        if (operation === "unload" && get().registry?.current_id === id) {
          const candidate = get().registry?.characters.find((role) => role.id !== id && role.status === "RUNNING");
          if (!candidate) throw new Error("至少保留一个可运行的当前角色");
          await get().select(candidate.id);
          if (get().registry?.current_id === id) return;
        }
        await changeCharacter(id, operation);
        await get().refresh();
      } catch (error) { set({ error: String(error) }); }
    },
    setDraft: (text) => {
      const id = get().registry?.current_id ?? "nyx";
      const drafts = { ...get().drafts, [id]: text };
      saveUi(drafts);
      set({ drafts });
    },
  };
});

useReaderStore.subscribe((state, previous) => {
  const selected = captureScope();
  if (selected && state.bookId && state.bookId !== previous.bookId) {
    lastBooks[selected.id] = state.bookId;
    saveUi(useCharacterStore.getState().drafts);
  }
});
