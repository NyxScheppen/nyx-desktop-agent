import { invoke } from "@tauri-apps/api/core";
import { create } from "zustand";
import {
  confirmGameChoice,
  getActivity,
  getGameSession,
  pauseGameCompanion,
  resumeGameCompanion,
  stopGameCompanion,
} from "../api/client";
import type {
  GameChoice,
  GameObservationSnapshot,
  GamePhase,
  GameProfile,
  GameSessionStatus,
  SseEvent,
} from "../types/api";

export type GameCompanionState = {
  sessionId: string | null;
  activityId: string | null;
  gameId: string | null;
  profile: GameProfile | null;
  profileVersion: number | null;
  thresholdVersion: number | null;
  status: GameSessionStatus | null;
  phase: GamePhase | null;
  revision: number;
  observationHash: string | null;
  observation: GameObservationSnapshot | null;
  corrections: unknown[];
  pendingChoice: GameChoice | null;
  remoteVisionEnabled: boolean;
  error: string | null;
  hydrate: (sessionId: string) => Promise<void>;
  loadCurrent: () => Promise<void>;
  acceptEvent: (event: SseEvent) => void;
  pause: () => Promise<void>;
  resume: () => Promise<void>;
  stop: () => Promise<void>;
  confirmChoice: (choiceId: string) => Promise<void>;
  openCompanion: () => Promise<void>;
  handleNativeWindowLost: () => Promise<void>;
  reset: () => void;
};

const INITIAL_STATE = {
  sessionId: null,
  activityId: null,
  gameId: null,
  profile: null,
  profileVersion: null,
  thresholdVersion: null,
  status: null,
  phase: null,
  revision: 0,
  observationHash: null,
  observation: null,
  corrections: [],
  pendingChoice: null,
  remoteVisionEnabled: false,
  error: null,
} satisfies Omit<GameCompanionState, "hydrate" | "loadCurrent" | "acceptEvent" | "pause" | "resume" | "stop" | "confirmChoice" | "openCompanion" | "handleNativeWindowLost" | "reset">;

function recordOf(value: unknown): Record<string, unknown> | null {
  return typeof value === "object" && value !== null
    ? (value as Record<string, unknown>)
    : null;
}

function stringValue(value: unknown): string | null {
  return typeof value === "string" && value.length > 0 ? value : null;
}

function numberValue(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function snapshotValue(value: unknown): GameObservationSnapshot | null {
  const raw = recordOf(value);
  if (
    raw === null ||
    typeof raw.session_id !== "string" ||
    typeof raw.revision !== "number" ||
    typeof raw.observation_hash !== "string" ||
    !Array.isArray(raw.choices) ||
    !Array.isArray(raw.dialogue)
  ) {
    return null;
  }
  return raw as unknown as GameObservationSnapshot;
}

function sessionFromRaw(raw: Record<string, unknown>): Partial<GameCompanionState> {
  const observation = snapshotValue(raw.last_observation);
  const revision =
    numberValue(raw.last_accepted_revision) ?? numberValue(raw.revision) ?? 0;
  return {
    sessionId: stringValue(raw.session_id),
    activityId: stringValue(raw.activity_id),
    gameId: stringValue(raw.game_id),
    profile: stringValue(raw.profile) as GameProfile | null,
    profileVersion: numberValue(raw.profile_version),
    thresholdVersion: numberValue(raw.threshold_version),
    status: stringValue(raw.status) as GameSessionStatus | null,
    phase: observation?.phase ?? null,
    revision,
    observationHash:
      stringValue(raw.last_observation_hash) ?? observation?.observation_hash ?? null,
    observation,
    corrections: Array.isArray(raw.corrections) ? raw.corrections : [],
    pendingChoice: null,
    remoteVisionEnabled: raw.remote_vision_enabled === true,
    error: null,
  };
}

function applyObservation(
  current: GameCompanionState,
  observation: GameObservationSnapshot,
): Partial<GameCompanionState> | null {
  if (observation.session_id !== current.sessionId || observation.revision < current.revision) {
    return null;
  }
  return {
    revision: observation.revision,
    phase: observation.phase,
    observationHash: observation.observation_hash,
    observation,
    pendingChoice: null,
    error: null,
  };
}

function errorText(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

function hasErrorCode(error: unknown, code: string): boolean {
  return errorText(error).includes(code);
}

function nativeErrorCode(error: unknown): string | null {
  const raw = recordOf(error);
  return raw === null || typeof raw.code !== "string" ? null : raw.code;
}

const NATIVE_ERROR_LABELS: Record<string, string> = {
  companion_window_create_failed: "陪玩窗口创建失败",
  companion_window_lost: "陪玩窗口已丢失",
  companion_window_not_found: "陪玩窗口不存在",
  companion_window_position_failed: "陪玩窗口定位失败",
  window_enumeration_failed: "游戏窗口枚举失败",
};

export const useGameCompanionStore = create<GameCompanionState>((set, get) => ({
  ...INITIAL_STATE,
  hydrate: async (sessionId) => {
    set({ error: null });
    try {
      const raw = recordOf(await getGameSession(sessionId));
      if (raw === null) throw new Error("陪玩会话响应非法");
      const hydrated = sessionFromRaw(raw);
      const hydratedSessionId = hydrated.sessionId ?? sessionId;
      const current = get();
      if (hydratedSessionId !== sessionId) throw new Error("陪玩会话响应非法");
      // REST 是断线恢复的旧快照；SSE 已经推进的 revision 不能被它回滚。
      if (
        (current.sessionId !== null && current.sessionId !== sessionId) ||
        (current.sessionId === sessionId && current.revision > (hydrated.revision ?? 0))
      ) return;
      if (
        current.sessionId === sessionId &&
        current.revision === (hydrated.revision ?? 0) &&
        current.observation !== null &&
        hydrated.observation === null
      ) {
        set({
          ...hydrated,
          sessionId,
          phase: current.phase,
          observationHash: current.observationHash,
          observation: current.observation,
        });
      } else {
        set({ ...hydrated, sessionId });
      }
    } catch (error) {
      set({ error: errorText(error) });
    }
  },
  loadCurrent: async () => {
    try {
      const data = await getActivity();
      const activities = [data.current, ...data.schedule].filter(
        (item): item is NonNullable<typeof item> => item !== null,
      );
      const current = activities.find(
        (item) =>
          item.type === "game_companion" &&
          (item.status === "running" || item.status === "paused"),
      );
      const progress = current === undefined ? null : recordOf(current.progress);
      const game = progress === null ? null : recordOf(progress.game_companion);
      const sessionId = game === null ? null : stringValue(game.session_id);
      if (sessionId !== null) await get().hydrate(sessionId);
    } catch (error) {
      set({ error: errorText(error) });
    }
  },
  acceptEvent: (event) => {
    const current = get();
    const data = event as unknown as Record<string, unknown>;
    if (event.event === "game_session_started") {
      const sessionId = stringValue(data.session_id);
      if (sessionId === null) return;
      if (current.sessionId === sessionId) return;
      set({
        ...sessionFromRaw(data),
        sessionId,
        status: "observing",
        revision: numberValue(data.revision) ?? 0,
        remoteVisionEnabled: data.remote_vision_enabled === true,
      });
      return;
    }
    if (stringValue(data.session_id) !== current.sessionId) return;
    if (event.event === "game_observation") {
      const observation = snapshotValue(data.observation_snapshot);
      if (observation === null) return;
      const update = applyObservation(current, observation);
      if (update !== null) set(update);
      return;
    }
    if (event.event === "game_choice_confirmed") {
      const revision = numberValue(data.revision);
      const choiceId = stringValue(data.choice_id);
      if (revision === null || choiceId === null || revision < current.revision) return;
      set({ pendingChoice: null });
      if (revision > current.revision && current.sessionId !== null) {
        void get().hydrate(current.sessionId);
      }
      return;
    }
    if (event.event === "game_observation_corrected") {
      if (current.corrections.some((item) => recordOf(item)?.event_id === event.event_id)) return;
      set({ corrections: [...current.corrections, data] });
    }
  },
  pause: async () => {
    const current = get();
    if (current.sessionId === null || current.status !== "observing") return;
    try {
      const result = await pauseGameCompanion(current.sessionId, current.revision);
      set({ status: result.status as GameSessionStatus, revision: result.revision, error: null });
    } catch (error) {
      set({ error: errorText(error) });
    }
  },
  resume: async () => {
    const current = get();
    if (current.sessionId === null || current.status !== "paused") return;
    try {
      const result = await resumeGameCompanion(current.sessionId, current.revision);
      set({ status: result.status as GameSessionStatus, revision: result.revision, error: null });
    } catch (error) {
      set({ error: errorText(error) });
    }
  },
  stop: async () => {
    const current = get();
    if (current.sessionId === null || current.status === "ended") return;
    try {
      const result = await stopGameCompanion(current.sessionId, current.revision);
      set({ status: result.status as GameSessionStatus, revision: result.revision, error: null });
    } catch (error) {
      set({ error: errorText(error) });
    }
  },
  confirmChoice: async (choiceId) => {
    const current = get();
    if (current.sessionId === null || current.status !== "observing") return;
    try {
      const result = await confirmGameChoice(current.sessionId, current.revision, choiceId);
      const observation = snapshotValue(result.current_observation);
      set({
        pendingChoice: null,
        ...(observation === null ? {} : applyObservation(get(), observation) ?? {}),
        error: null,
      });
    } catch (error) {
      if (
        hasErrorCode(error, "stale_choice") &&
        get().sessionId === current.sessionId
      ) {
        const staleMessage = errorText(error);
        await get().hydrate(current.sessionId);
        const hydrateError = get().error;
        set({
          error:
            hydrateError === null
              ? staleMessage
              : `${staleMessage}；恢复最新陪玩状态失败：${hydrateError}`,
        });
        return;
      }
      set({ error: errorText(error) });
    }
  },
  openCompanion: async () => {
    set({ error: null });
    try {
      await invoke("game_companion_open");
    } catch (error) {
      const code = nativeErrorCode(error);
      set({
        error: code === null ? errorText(error) : NATIVE_ERROR_LABELS[code] ?? "陪玩窗口操作失败",
      });
    }
  },
  handleNativeWindowLost: async () => {
    const current = get();
    if (current.sessionId === null || current.status !== "observing") return;
    const sessionId = current.sessionId;
    const pauseCurrent = async (): Promise<boolean> => {
      const latest = get();
      if (latest.sessionId !== sessionId || latest.status !== "observing") return true;
      try {
        const result = await pauseGameCompanion(latest.sessionId, latest.revision);
        set({ status: result.status as GameSessionStatus, revision: result.revision,
          error: "陪玩窗口已关闭，观察已暂停" });
        return true;
      } catch {
        return false;
      }
    };
    if (await pauseCurrent()) return;
    await get().hydrate(sessionId);
    if (!(await pauseCurrent())) set({ error: "陪玩窗口已关闭，但暂停观察失败" });
  },
  reset: () => set({ ...INITIAL_STATE }),
}));
