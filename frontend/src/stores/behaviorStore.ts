import { create } from "zustand";
import { captureScope, isScopeCurrent } from "../api/characterScope";
import { getBehaviorReflections, getResearchRuns, getSkillVersions } from "../api/client";

export type ResearchRun = {
  id: string; question: string; status: string; correlation_id: string;
  origin: string; created_at: number; finished_at?: number;
  result?: Record<string, unknown>; last_error?: string;
  activity: { progress: { exploration: {
    actions?: Record<string, unknown>[];
    node_trace?: Record<string, unknown>[];
    waiting_question?: string;
  } } };
};
export type BehaviorReflection = {
  id: string; status: string; target_skill: string; used_skill_version: number;
  created_at: number; reflection_started_at?: number; persisted_at?: number;
  skill_updated_at?: number; applied_skill_version?: number;
  skill_update_status: string; skill_diff?: string;
  evidence: Record<string, unknown>; reflection?: Record<string, unknown>;
  reflection_output_raw?: string; experience_content_raw?: string;
  model_attempts?: Record<string, unknown>[];
  logs: Record<string, unknown>[]; last_error?: string;
};
export type SkillVersion = {
  version: number; body: string; reflection_id: string | null; created_at: number;
};
type BehaviorState = {
  runs: ResearchRun[]; reflections: BehaviorReflection[];
  versions: Record<string, SkillVersion[]>; error: string | null;
  refresh: () => Promise<void>;
};

let generation = 0;
export const useBehaviorStore = create<BehaviorState>((set) => ({
  runs: [], reflections: [], versions: {}, error: null,
  refresh: async () => {
    const scope = captureScope();
    const current = ++generation;
    try {
      const [runs, reflections, discussion, conversation] = await Promise.all([
        getResearchRuns(), getBehaviorReflections(),
        getSkillVersions("discussion"), getSkillVersions("conversation"),
      ]);
      if (!isScopeCurrent(scope) || current !== generation) return;
      set({ runs, reflections, versions: { discussion, conversation }, error: null });
    } catch (error) {
      if (!isScopeCurrent(scope) || current !== generation) return;
      set({ error: error instanceof Error ? error.message : String(error) });
    }
  },
}));
