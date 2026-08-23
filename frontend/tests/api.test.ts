import { afterEach, describe, expect, it, vi } from "vitest";
import {
  exportMemories,
  getActivity,
  getActivityResults,
  getDesires,
  getEval,
  getEventsLog,
  getMaterials,
  getMemories,
  getNarrative,
  getState,
  getTokens,
  postChat,
  postObserve,
  uploadFile,
} from "../src/api/client";

function jsonResponse(body: unknown, ok = true, status = 200): Response {
  return { ok, status, json: async () => body } as Response;
}

function textResponse(body: string, ok = true, status = 200): Response {
  return { ok, status, text: async () => body } as Response;
}

const snapshot = {
  valence: 0.1,
  arousal: 0.2,
  emotion: "neutral",
  personality: {
    openness: 8,
    conscientiousness: 8,
    extraversion: 2,
    agreeableness: 6,
    neuroticism: 7,
  },
  values: {
    attitude_to_human: 8,
    ai_identity_acceptance: 6,
    altruism: 9,
    optimism: 5,
  },
  energy: 100,
  energy_state: "energetic",
  current_activity: null,
  active_desires: [],
};

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("api/client", () => {
  it("postChat：POST /api/chat、body {message}、解析 {event_id}", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ event_id: "e1" }));
    vi.stubGlobal("fetch", fetchMock);

    const res = await postChat("你好");

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/chat");
    expect(init).toMatchObject({
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message: "你好" }),
    });
    expect(res).toEqual({ event_id: "e1" });
  });

  it("getState：GET /api/state、解析 CurrentState", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(snapshot));
    vi.stubGlobal("fetch", fetchMock);

    const res = await getState();

    expect(fetchMock.mock.calls[0][0]).toBe("/api/state");
    expect(res).toEqual(snapshot);
  });

  it("postObserve：POST /api/observe、body {presence, window_title}、解析 {event_id}", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ event_id: "e2" }));
    vi.stubGlobal("fetch", fetchMock);

    const res = await postObserve("away", "编辑器");

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/observe");
    expect(init).toMatchObject({
      method: "POST",
      body: JSON.stringify({ presence: "away", window_title: "编辑器" }),
    });
    expect(res).toEqual({ event_id: "e2" });
  });

  it("非 2xx：读 body.detail 上抛 Error（message 含 detail）", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(jsonResponse({ detail: "校验失败" }, false, 422)),
    );

    await expect(getState()).rejects.toThrow("校验失败");
  });

  it("非 2xx 无 detail：兜底 JSON.stringify(body) 非空", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(jsonResponse({ whatever: 1 }, false, 500)),
    );

    await expect(getState()).rejects.toThrow('{"whatever":1}');
  });

  it("非 2xx detail 空串：兜底 HTTP status（非空 message）", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(jsonResponse({ detail: "" }, false, 500)),
    );

    await expect(getState()).rejects.toThrow("HTTP 500");
  });

  it("fetch 网络错误（TypeError）上抛不吞", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockRejectedValue(new TypeError("fetch failed")),
    );

    await expect(getState()).rejects.toThrow(TypeError);
  });

  it("getDesires：GET /api/desires、解析 DesireState", async () => {
    const fixture = { values: [], short_term: [], long_term: [] };
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(fixture));
    vi.stubGlobal("fetch", fetchMock);

    const res = await getDesires();

    expect(fetchMock.mock.calls[0][0]).toBe("/api/desires");
    expect(res).toEqual(fixture);
  });

  it("getActivity：GET /api/activity、解析 ActivitySnapshot", async () => {
    const fixture = { current: null, schedule: [] };
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(fixture));
    vi.stubGlobal("fetch", fetchMock);

    const res = await getActivity();

    expect(fetchMock.mock.calls[0][0]).toBe("/api/activity");
    expect(res).toEqual(fixture);
  });

  it("getActivityResults：GET /api/activity/results、解析 Activity[]", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse([]));
    vi.stubGlobal("fetch", fetchMock);

    const res = await getActivityResults();

    expect(fetchMock.mock.calls[0][0]).toBe("/api/activity/results");
    expect(res).toEqual([]);
  });

  it("getMemories：query 参数拼装（tag/type 可选）", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse([]));
    vi.stubGlobal("fetch", fetchMock);

    await getMemories("user", "long_term");

    expect(fetchMock.mock.calls[0][0]).toBe("/api/memories?tag=user&type=long_term");
  });

  it("getMemories：无参数时不带 query string", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse([]));
    vi.stubGlobal("fetch", fetchMock);

    await getMemories();

    expect(fetchMock.mock.calls[0][0]).toBe("/api/memories");
  });

  it("getEval：可选 limit 拼进 query", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse([]));
    vi.stubGlobal("fetch", fetchMock);

    await getEval(5);

    expect(fetchMock.mock.calls[0][0]).toBe("/api/eval?limit=5");
  });

  it("getTokens：可选 since 拼进 query", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse([]));
    vi.stubGlobal("fetch", fetchMock);

    await getTokens(1000);

    expect(fetchMock.mock.calls[0][0]).toBe("/api/tokens?since=1000");
  });

  it("getEventsLog：limit/event_type/correlation_id 拼进 query", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse([]));
    vi.stubGlobal("fetch", fetchMock);

    await getEventsLog({ limit: 20, event_type: "speak", correlation_id: "c1" });

    expect(fetchMock.mock.calls[0][0]).toBe(
      "/api/events/log?limit=20&event_type=speak&correlation_id=c1",
    );
  });

  it("getNarrative：GET /api/narrative、解析 SelfNarrative", async () => {
    const fixture = {
      identity: "我",
      story: ["a"],
      self_view: { k: "v" },
      becoming: ["b"],
      updated_at: 123,
    };
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(fixture));
    vi.stubGlobal("fetch", fetchMock);

    const res = await getNarrative();

    expect(fetchMock.mock.calls[0][0]).toBe("/api/narrative");
    expect(res).toEqual(fixture);
  });

  it("exportMemories：POST /api/export、body {format}、返回 text", async () => {
    const fetchMock = vi.fn().mockResolvedValue(textResponse("# 记忆\n..."));
    vi.stubGlobal("fetch", fetchMock);

    const res = await exportMemories("md");

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/export");
    expect(init).toMatchObject({
      method: "POST",
      body: JSON.stringify({ format: "md" }),
    });
    expect(res).toBe("# 记忆\n...");
  });

  it("uploadFile：POST /api/upload、FormData 带 file、解析 UploadResult", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      jsonResponse({
        event_id: "e1",
        filename: "book.txt",
        path: "workspace/uploads/book.txt",
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const res = await uploadFile(
      new File(["内容"], "book.txt", { type: "text/plain" }),
    );

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/upload");
    expect(init).toMatchObject({ method: "POST" });
    expect(init?.body).toBeInstanceOf(FormData);
    expect(res).toEqual({
      event_id: "e1",
      filename: "book.txt",
      path: "workspace/uploads/book.txt",
    });
  });

  it("getMaterials：GET /api/materials、解析 {materials: Material[]}", async () => {
    const fixture = {
      materials: [
        {
          path: "workspace/uploads/a.txt",
          filename: "a.txt",
          total_chars: 100,
          read_chars: 40,
          created_at: 1,
          updated_at: 2,
        },
      ],
    };
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(fixture));
    vi.stubGlobal("fetch", fetchMock);

    const res = await getMaterials();

    expect(fetchMock.mock.calls[0][0]).toBe("/api/materials");
    expect(res).toEqual(fixture);
  });
});
