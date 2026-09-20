import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { dispatchEvent } from "../src/api/dispatch";
import GameCompanionView from "../src/components/game/GameCompanionView";
import { useGameCompanionStore } from "../src/stores/gameCompanionStore";

const snapshot = {
  session_id: "s1",
  game_id: "disco_elysium",
  profile: "disco_elysium" as const,
  profile_version: 1,
  threshold_version: 1,
  revision: 2,
  phase: "dialogue" as const,
  observation_hash: "sha256:h2",
  captured_at: 2,
  speaker: "Kim",
  speaker_evidence_ids: ["e1"],
  dialogue: [],
  text_blocks: [],
  choices: [{ id: "c1", text: "继续", order: 0, bbox: null, confidence: 0.9, evidence_ids: ["e1"] }],
  visible_entities: [],
  entity_evidence_ids: [],
  scene_summary: "走廊里的对话",
  scene_evidence_ids: [],
  confidence: 0.9,
  evidence: [],
  uncertainties: [],
};

const rawSession = {
  activity_id: "a1",
  session_id: "s1",
  game_id: "disco_elysium",
  profile: "disco_elysium",
  profile_version: 1,
  threshold_version: 1,
  status: "observing",
  last_accepted_revision: 2,
  last_observation_hash: "sha256:h2",
  last_observation: snapshot,
  corrections: [],
  remote_vision_enabled: false,
};

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  useGameCompanionStore.setState(useGameCompanionStore.getInitialState(), true);
});

describe("gameCompanionStore", () => {
  it("hydrates the durable checkpoint and maps last_accepted_revision", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => rawSession,
    }));

    await useGameCompanionStore.getState().hydrate("s1");

    expect(useGameCompanionStore.getState()).toMatchObject({
      sessionId: "s1",
      activityId: "a1",
      revision: 2,
      observationHash: "sha256:h2",
      observation: snapshot,
      status: "observing",
    });
  });

  it("ignores an older game observation event", () => {
    useGameCompanionStore.setState({
      sessionId: "s1",
      revision: 2,
      observation: snapshot,
      observationHash: "sha256:h2",
    });

    useGameCompanionStore.getState().acceptEvent({
      event: "game_observation",
      event_id: "e-old",
      correlation_id: "s1",
      timestamp: 3,
      session_id: "s1",
      revision: 1,
      observation_snapshot: { ...snapshot, revision: 1, observation_hash: "sha256:h1" },
    });

    expect(useGameCompanionStore.getState().revision).toBe(2);
    expect(useGameCompanionStore.getState().observationHash).toBe("sha256:h2");
  });

  it("routes game observation SSE through dispatchEvent", () => {
    useGameCompanionStore.setState({ sessionId: "s1", revision: 1 });

    dispatchEvent({
      event: "game_observation",
      event_id: "e2",
      correlation_id: "s1",
      timestamp: 4,
      session_id: "s1",
      revision: 2,
      observation_snapshot: snapshot,
    });

    expect(useGameCompanionStore.getState().revision).toBe(2);
  });

  it("does not let an older hydrate response overwrite a newer SSE observation", async () => {
    let resolveHydrate: ((value: Response) => void) | undefined;
    vi.stubGlobal("fetch", vi.fn().mockReturnValue(
      new Promise<Response>((resolve) => { resolveHydrate = resolve; }),
    ));
    useGameCompanionStore.setState({
      sessionId: "s1",
      revision: 2,
      observation: snapshot,
      observationHash: snapshot.observation_hash,
    });

    const pending = useGameCompanionStore.getState().hydrate("s1");
    useGameCompanionStore.getState().acceptEvent({
      event: "game_observation",
      event_id: "e3",
      correlation_id: "s1",
      timestamp: 5,
      session_id: "s1",
      revision: 3,
      observation_snapshot: { ...snapshot, revision: 3, observation_hash: "sha256:h3" },
    });
    resolveHydrate?.(new Response(JSON.stringify(rawSession), { status: 200 }));
    await pending;

    expect(useGameCompanionStore.getState().revision).toBe(3);
    expect(useGameCompanionStore.getState().observationHash).toBe("sha256:h3");
  });

  it("hydrates after stale choice and keeps an explicit stale error", async () => {
    const refreshed = { ...rawSession, last_accepted_revision: 3 };
    vi.stubGlobal("fetch", vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({ detail: "stale_choice" }), { status: 409 }))
      .mockResolvedValueOnce(new Response(JSON.stringify(refreshed), { status: 200 })));
    useGameCompanionStore.setState({ sessionId: "s1", revision: 2 });

    await useGameCompanionStore.getState().confirmChoice("c1");

    expect(useGameCompanionStore.getState().revision).toBe(3);
    expect(useGameCompanionStore.getState().error).toContain("stale_choice");
  });

  it("loads the active game session from the activity snapshot", async () => {
    const activity = {
      current: {
        id: "a1",
        type: "game_companion",
        schedule_block_id: "b1",
        status: "running",
        progress: { game_companion: { session_id: "s1" } },
        started_at: 1,
        ended_at: null,
      },
      schedule: [],
    };
    vi.stubGlobal("fetch", vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify(activity), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify(rawSession), { status: 200 })));

    await useGameCompanionStore.getState().loadCurrent();

    expect(useGameCompanionStore.getState()).toMatchObject({ sessionId: "s1", revision: 2 });
  });
});

describe("GameCompanionView", () => {
  it("renders the current scene and confirms a visible choice", async () => {
    const confirmChoice = vi.fn().mockResolvedValue(undefined);
    useGameCompanionStore.setState({
      sessionId: "s1",
      gameId: "disco_elysium",
      profile: "disco_elysium",
      activityId: "a1",
      status: "observing",
      revision: 2,
      observation: snapshot,
      observationHash: snapshot.observation_hash,
      confirmChoice,
    });

    render(<GameCompanionView />);

    expect(screen.getByText("走廊里的对话")).toBeInTheDocument();
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "继续" }));
    });
    await waitFor(() => expect(confirmChoice).toHaveBeenCalledWith("c1"));
  });
});
