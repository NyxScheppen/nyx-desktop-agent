import { afterEach, describe, expect, it, vi } from "vitest";
import {
  checkChapterBoundary,
  createUserNote,
  deleteUserNote,
  evaluateImpulse,
  getActivity,
  getActivityResults,
  getBookParagraphs,
  getBooks,
  getDesires,
  getEventsLog,
  getNotes,
  getProgress,
  getState,
  importBook,
  postChat,
  postObserve,
  putProgress,
  showNoteToNyx,
  updateUserNote,
} from "../src/api/client";

function jsonResponse(body: unknown, ok = true, status = 200): Response {
  return { ok, status, json: async () => body } as Response;
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

  it("getEventsLog：limit/event_type/correlation_id 拼进 query", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse([]));
    vi.stubGlobal("fetch", fetchMock);

    await getEventsLog({ limit: 20, event_type: "speak", correlation_id: "c1" });

    expect(fetchMock.mock.calls[0][0]).toBe(
      "/api/events/log?limit=20&event_type=speak&correlation_id=c1",
    );
  });
});

describe("api/client reading", () => {
  const book: Record<string, unknown> = {
    id: "b1",
    title: "挪威的森林",
    author: "村上春树",
    filename: "norway.epub",
    content_hash: "abc",
    total_paragraphs: 120,
    created_at: 1,
    updated_at: 2,
  };

  it("getBooks：GET /api/books、解析 BookListItem[]", async () => {
    const fixture = [{ id: "b1", title: "挪威的森林", author: "村上春树", filename: "norway.epub", total_paragraphs: 120, user_position: 3, last_read_at: 1000 }];
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(fixture));
    vi.stubGlobal("fetch", fetchMock);

    const res = await getBooks();

    expect(fetchMock.mock.calls[0][0]).toBe("/api/books");
    expect(res).toEqual(fixture);
  });

  it("getBookParagraphs：from/to 拼进 query", async () => {
    const fixture = [{ id: "p1", book_id: "b1", index: 1, text: "开头", is_chapter_start: true }];
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(fixture));
    vi.stubGlobal("fetch", fetchMock);

    const res = await getBookParagraphs("b1", 1, 50);

    expect(fetchMock.mock.calls[0][0]).toBe("/api/books/b1/paragraphs?from=1&to=50");
    expect(res).toEqual(fixture);
  });

  it("getProgress：GET /api/progress/{id}、解析 Progress", async () => {
    const fixture = { user_position: 3, nyx_position: 1, reading_speed: 50, read_count: 0 };
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(fixture));
    vi.stubGlobal("fetch", fetchMock);

    const res = await getProgress("b1");

    expect(fetchMock.mock.calls[0][0]).toBe("/api/progress/b1");
    expect(res).toEqual(fixture);
  });

  it("putProgress：PUT + body 三键 {user_position, nyx_position, reading_speed}", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ ok: true }));
    vi.stubGlobal("fetch", fetchMock);

    await putProgress("b1", { user_position: 4, nyx_position: 2, reading_speed: 60 });

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/progress/b1");
    expect(init).toMatchObject({
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ user_position: 4, nyx_position: 2, reading_speed: 60 }),
    });
  });

  it("importBook：POST /api/books、FormData 带 file 字段、不设 json 头", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(book));
    vi.stubGlobal("fetch", fetchMock);
    const file = new File(["<p>hi</p>"], "book.epub");

    const res = await importBook(file);

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/books");
    expect(init).toMatchObject({ method: "POST" });
    expect(init.headers).toBeUndefined();
    expect(init.body).toBeInstanceOf(FormData);
    expect((init.body as FormData).get("file")).toBe(file);
    expect(res).toEqual(book);
  });

  it("evaluateImpulse：POST /api/impulse/evaluate、body 三键 snake_case", async () => {
    const fixture = { triggered: ["reading_mutter"] };
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(fixture));
    vi.stubGlobal("fetch", fetchMock);

    const res = await evaluateImpulse("b1", 5, 4);

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/impulse/evaluate");
    expect(init).toMatchObject({
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ book_id: "b1", paragraph_index: 5, last_paragraph_index: 4 }),
    });
    expect(res).toEqual(fixture);
  });

  it("checkChapterBoundary：POST /api/notes/check-chapter-boundary、body {book_id, nyx_position}", async () => {
    const fixture = { is_boundary: true, book_finished: false };
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(fixture));
    vi.stubGlobal("fetch", fetchMock);

    const res = await checkChapterBoundary("b1", 12);

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/notes/check-chapter-boundary");
    expect(init).toMatchObject({
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ book_id: "b1", nyx_position: 12 }),
    });
    expect(res).toEqual(fixture);
  });
});

describe("api/client notes", () => {
  const note = {
    id: "n1",
    book_id: "b1",
    paragraph_id: "p1",
    content: "这条笔记",
    selected_text: "划线原文",
    created_at: 1,
    updated_at: 2,
  };
  const noteWithAnn = {
    ...note,
    annotations: [{ id: "a1", user_note_id: "n1", content: "批注", created_at: 3 }],
  };
  const annotation = { id: "a1", user_note_id: "n1", content: "批注", created_at: 3 };

  it("getNotes：GET /api/notes/{bookId}、解析 UserNoteWithAnnotations[]", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse([noteWithAnn]));
    vi.stubGlobal("fetch", fetchMock);

    const res = await getNotes("b1");

    expect(fetchMock.mock.calls[0][0]).toBe("/api/notes/b1");
    expect(res).toEqual([noteWithAnn]);
  });

  it("createUserNote：POST /api/notes/user、body 四键 snake_case", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(note));
    vi.stubGlobal("fetch", fetchMock);

    const res = await createUserNote({
      book_id: "b1",
      paragraph_id: "p1",
      content: "这条笔记",
      selected_text: "划线原文",
    });

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/notes/user");
    expect(init).toMatchObject({
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        book_id: "b1",
        paragraph_id: "p1",
        content: "这条笔记",
        selected_text: "划线原文",
      }),
    });
    expect(res).toEqual(note);
  });

  it("updateUserNote：PUT /api/notes/user/{id}、body {content}", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(note));
    vi.stubGlobal("fetch", fetchMock);

    const res = await updateUserNote("n1", "改后");

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/notes/user/n1");
    expect(init).toMatchObject({
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ content: "改后" }),
    });
    expect(res).toEqual(note);
  });

  it("deleteUserNote：DELETE /api/notes/user/{id}（204 不解析 body）", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 204,
      json: async () => {
        throw new Error("204 无 body，不该调 json");
      },
    } as unknown as Response);
    vi.stubGlobal("fetch", fetchMock);

    await expect(deleteUserNote("n1")).resolves.toBeUndefined();
    expect(fetchMock.mock.calls[0][0]).toBe("/api/notes/user/n1");
    expect(fetchMock.mock.calls[0][1]).toMatchObject({ method: "DELETE" });
  });

  it("showNoteToNyx：POST /api/notes/{noteId}/show-to-nyx、解析 Annotation", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(annotation));
    vi.stubGlobal("fetch", fetchMock);

    const res = await showNoteToNyx("n1");

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/notes/n1/show-to-nyx");
    expect(init).toMatchObject({ method: "POST" });
    expect(res).toEqual(annotation);
  });

  it("showNoteToNyx：LLM 空回 null（不反噬）", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(null));
    vi.stubGlobal("fetch", fetchMock);

    const res = await showNoteToNyx("n1");

    expect(res).toBeNull();
  });

  it("createUserNote 422：读 body.detail 上抛", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(jsonResponse({ detail: "content 不能为空" }, false, 422)),
    );

    await expect(createUserNote({ book_id: "b1", content: "" })).rejects.toThrow(
      "content 不能为空",
    );
  });
});
