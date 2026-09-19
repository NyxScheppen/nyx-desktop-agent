import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { invoke } from "@tauri-apps/api/core";
import { dispatchEvent } from "../src/api/dispatch";
import GameCompanionView from "../src/components/game/GameCompanionView";
import { useGameCompanionStore } from "../src/stores/gameCompanionStore";

vi.mock("@tauri-apps/api/core", () => ({ invoke: vi.fn() }));

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
  vi.mocked(invoke).mockReset();
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
    useGameCompanionStore.setState({ sessionId: "s1", status: "observing", revision: 2 });

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

  it("does not restore abandoned game activities", async () => {
    const activity = {
      current: {
        id: "a-old",
        type: "game_companion",
        schedule_block_id: "b1",
        status: "abandoned",
        progress: { game_companion: { session_id: "old-session" } },
        started_at: 1,
        ended_at: 2,
      },
      schedule: [],
    };
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify(activity), { status: 200 }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await useGameCompanionStore.getState().loadCurrent();

    expect(useGameCompanionStore.getState().sessionId).toBeNull();
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("does not let a late session-start event reset a hydrated session", () => {
    useGameCompanionStore.setState({
      sessionId: "s1",
      revision: 3,
      observation: { ...snapshot, revision: 3, observation_hash: "sha256:h3" },
      observationHash: "sha256:h3",
      status: "observing",
    });

    useGameCompanionStore.getState().acceptEvent({
      event: "game_session_started",
      event_id: "start-late",
      correlation_id: "s1",
      timestamp: 6,
      session_id: "s1",
      activity_id: "a1",
      game_id: "disco_elysium",
      profile: "disco_elysium",
      profile_version: 1,
      threshold_version: 1,
      revision: 0,
      remote_vision_enabled: false,
    });

    expect(useGameCompanionStore.getState()).toMatchObject({
      sessionId: "s1",
      revision: 3,
      observationHash: "sha256:h3",
    });
  });

  it("does not submit a choice after the session has ended", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    useGameCompanionStore.setState({ sessionId: "s1", status: "ended", revision: 2 });

    await useGameCompanionStore.getState().confirmChoice("c1");

    expect(fetchMock).not.toHaveBeenCalled();
  });

  it.each([
    ["pause", "observing", "paused", "pause"],
    ["resume", "paused", "observing", "resume"],
    ["stop", "observing", "ended", "stop"],
  ] as const)("sends expected_revision for %s", async (action, status, nextStatus, path) => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ status: nextStatus, revision: 2 }), { status: 200 }),
    );
    vi.stubGlobal("fetch", fetchMock);
    useGameCompanionStore.setState({ sessionId: "s1", status, revision: 2 });

    await useGameCompanionStore.getState()[action]();

    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining(`/sessions/s1/${path}`),
      expect.objectContaining({ body: JSON.stringify({ expected_revision: 2 }) }),
    );
  });

  it("opens the companion window through the native command", async () => {
    vi.mocked(invoke).mockResolvedValue(undefined);

    await useGameCompanionStore.getState().openCompanion();

    expect(invoke).toHaveBeenCalledWith("game_companion_open");
    expect(useGameCompanionStore.getState().error).toBeNull();
  });

  it("maps native companion errors by code", async () => {
    vi.mocked(invoke).mockRejectedValue({ code: "companion_window_create_failed" });

    await useGameCompanionStore.getState().openCompanion();

    expect(useGameCompanionStore.getState().error).toBe("陪玩窗口创建失败");
  });
});

describe("GameCompanionView", () => {
  it("shows the native companion window entry in the main panel", async () => {
    render(<GameCompanionView />);

    expect(screen.getByRole("button", { name: "打开陪玩窗口" })).toBeInTheDocument();
    await waitFor(() => expect(useGameCompanionStore.getState().error).toBeNull());
  });

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
