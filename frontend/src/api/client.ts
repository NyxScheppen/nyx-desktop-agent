import type {
  Activity,
  ActivitySnapshot,
  Annotation,
  BackendEvent,
  Book,
  BookListItem,
  CurrentState,
  DesireState,
  EvalRecord,
  EvalStats,
  Memory,
  Paragraph,
  Presence,
  Progress,
  ProgressInput,
  UserNote,
  UserNoteWithAnnotations,
} from "../types/api";

// 空 = 相对路径，走 Vite proxy 同源转发到后端 8000（18-api 不做 CORS，localhost 同源）
export const BASE_URL = "";

// 统一错误契约（05-client §2）：成功返回数据、失败 throw，不包裹 {ok, data}。
// fetch 网络错误（TypeError）自然上抛不吞；非 2xx 读 body.detail ?? body.error ?? JSON.stringify(body)，
// 兜底保证 Error.message 非空。
async function assertOk(res: Response): Promise<void> {
  if (res.ok) return;
  let detail: unknown;
  try {
    const body = (await res.json()) as Record<string, unknown>;
    detail = body.detail ?? body.error ?? JSON.stringify(body);
  } catch {
    detail = `HTTP ${res.status}`;
  }
  const message = typeof detail === "string" ? detail : JSON.stringify(detail);
  throw new Error(message || `HTTP ${res.status}`); // 空串兜底：防 UI if(sendError) 误判为无错误
}

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, init);
  await assertOk(res);
  return (await res.json()) as T;
}

export async function postChat(message: string): Promise<{ event_id: string }> {
  return request<{ event_id: string }>(`${BASE_URL}/api/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message }),
  });
}

export async function getState(): Promise<CurrentState> {
  return request<CurrentState>(`${BASE_URL}/api/state`);
}

export async function postObserve(
  presence: Presence,
  windowTitle: string,
): Promise<{ event_id: string }> {
  return request<{ event_id: string }>(`${BASE_URL}/api/observe`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ presence, window_title: windowTitle }),
  });
}

export async function getDesires(): Promise<DesireState> {
  return request<DesireState>(`${BASE_URL}/api/desires`);
}

export async function getMemories(): Promise<Memory[]> {
  return request<Memory[]>(`${BASE_URL}/api/memories`);
}

export async function getActivity(): Promise<ActivitySnapshot> {
  return request<ActivitySnapshot>(`${BASE_URL}/api/activity`);
}

export async function getActivityResults(): Promise<Activity[]> {
  return request<Activity[]>(`${BASE_URL}/api/activity/results`);
}

export async function getEventsLog(params?: {
  limit?: number;
  event_type?: string;
  correlation_id?: string;
}): Promise<BackendEvent[]> {
  const sp = new URLSearchParams();
  if (params?.limit !== undefined) sp.set("limit", String(params.limit));
  if (params?.event_type !== undefined) sp.set("event_type", params.event_type);
  if (params?.correlation_id !== undefined)
    sp.set("correlation_id", params.correlation_id);
  const qs = sp.toString();
  return request<BackendEvent[]>(`${BASE_URL}/api/events/log${qs ? `?${qs}` : ""}`);
}

// ---- 阅读（19/20/21-reading，06-reading-panel §6）----

export async function getBooks(): Promise<BookListItem[]> {
  return request<BookListItem[]>(`${BASE_URL}/api/books`);
}

export async function getBookParagraphs(
  bookId: string,
  from: number,
  to: number,
): Promise<Paragraph[]> {
  return request<Paragraph[]>(
    `${BASE_URL}/api/books/${bookId}/paragraphs?from=${from}&to=${to}`,
  );
}

export async function getProgress(bookId: string): Promise<Progress> {
  return request<Progress>(`${BASE_URL}/api/progress/${bookId}`);
}

export async function putProgress(
  bookId: string,
  p: ProgressInput,
): Promise<void> {
  await request<{ ok: boolean }>(`${BASE_URL}/api/progress/${bookId}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(p),
  });
}

// 唯一 multipart 端点：FormData + file 字段，不设 Content-Type（浏览器自动带 boundary）。
export async function importBook(file: File): Promise<Book> {
  const form = new FormData();
  form.append("file", file);
  return request<Book>(`${BASE_URL}/api/books`, { method: "POST", body: form });
}

export async function evaluateImpulse(
  bookId: string,
  paragraphIndex: number,
  lastParagraphIndex: number,
): Promise<{ triggered: string[] }> {
  return request<{ triggered: string[] }>(`${BASE_URL}/api/impulse/evaluate`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      book_id: bookId,
      paragraph_index: paragraphIndex,
      last_paragraph_index: lastParagraphIndex,
    }),
  });
}

// 章末/整本检测（22-reading-notes，07-reading-events §4 定义，06 追赶循环调用）。
export async function checkChapterBoundary(
  bookId: string,
  nyxPosition: number,
): Promise<{ is_boundary: boolean; book_finished: boolean }> {
  return request<{ is_boundary: boolean; book_finished: boolean }>(
    `${BASE_URL}/api/notes/check-chapter-boundary`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ book_id: bookId, nyx_position: nyxPosition }),
    },
  );
}

// ---- 笔记（22-reading-notes，07-reading-events §4）----

export async function getNotes(bookId: string): Promise<UserNoteWithAnnotations[]> {
  return request<UserNoteWithAnnotations[]>(`${BASE_URL}/api/notes/${bookId}`);
}

export async function createUserNote(p: {
  book_id: string;
  paragraph_id?: string | null;
  content: string;
  selected_text?: string | null;
}): Promise<UserNote> {
  return request<UserNote>(`${BASE_URL}/api/notes/user`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(p),
  });
}

export async function updateUserNote(id: string, content: string): Promise<UserNote> {
  return request<UserNote>(`${BASE_URL}/api/notes/user/${id}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ content }),
  });
}

// 204 No Content：无 body，走 request() 会 res.json() 抛，这里直接 fetch + assertOk。
export async function deleteUserNote(id: string): Promise<void> {
  const res = await fetch(`${BASE_URL}/api/notes/user/${id}`, { method: "DELETE" });
  await assertOk(res);
}

// 后端 show_to_nyx LLM 空/失败回 null（200 null，非错误），故返回 Annotation | null。
export async function showNoteToNyx(noteId: string): Promise<Annotation | null> {
  return request<Annotation | null>(`${BASE_URL}/api/notes/${noteId}/show-to-nyx`, {
    method: "POST",
  });
}

// ---- eval 记账（15-eval，06-settings-panel §7）----

export async function getEvalRecent(limit = 5): Promise<EvalRecord[]> {
  return request<EvalRecord[]>(`${BASE_URL}/api/eval/recent?limit=${limit}`);
}

export async function getEvalTotalTokens(): Promise<EvalStats> {
  return request<EvalStats>(`${BASE_URL}/api/eval/total_tokens`);
}
