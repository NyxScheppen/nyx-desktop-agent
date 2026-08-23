import type {
  Activity,
  ActivitySnapshot,
  Annotation,
  BackendEvent,
  CurrentState,
  DesireState,
  EvalReport,
  Material,
  Memory,
  MemoryType,
  Presence,
  ReadingNote,
  SelfNarrative,
  TokenUsage,
  UploadResult,
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

export async function getActivity(): Promise<ActivitySnapshot> {
  return request<ActivitySnapshot>(`${BASE_URL}/api/activity`);
}

export async function getActivityResults(): Promise<Activity[]> {
  return request<Activity[]>(`${BASE_URL}/api/activity/results`);
}

export async function getMemories(
  tag?: string,
  type?: MemoryType,
): Promise<Memory[]> {
  const params = new URLSearchParams();
  if (tag !== undefined) params.set("tag", tag);
  if (type !== undefined) params.set("type", type);
  const qs = params.toString();
  return request<Memory[]>(`${BASE_URL}/api/memories${qs ? `?${qs}` : ""}`);
}

export async function getEval(limit?: number): Promise<EvalReport[]> {
  const qs = limit !== undefined ? `?limit=${limit}` : "";
  return request<EvalReport[]>(`${BASE_URL}/api/eval${qs}`);
}

export async function getTokens(since?: number): Promise<TokenUsage[]> {
  const qs = since !== undefined ? `?since=${since}` : "";
  return request<TokenUsage[]>(`${BASE_URL}/api/tokens${qs}`);
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

export async function getNarrative(): Promise<SelfNarrative> {
  return request<SelfNarrative>(`${BASE_URL}/api/narrative`);
}

export async function exportMemories(format: "json" | "md"): Promise<string> {
  const res = await fetch(`${BASE_URL}/api/export`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ format }),
  });
  await assertOk(res);
  return await res.text();
}

export async function uploadFile(file: File): Promise<UploadResult> {
  const form = new FormData();
  form.append("file", file);
  const res = await fetch(`${BASE_URL}/api/upload`, { method: "POST", body: form });
  await assertOk(res);
  return (await res.json()) as UploadResult;
}

export async function getMaterials(): Promise<{ materials: Material[] }> {
  return request<{ materials: Material[] }>(`${BASE_URL}/api/materials`);
}

export async function getReadingNotes(limit?: number): Promise<ReadingNote[]> {
  const qs = limit !== undefined ? `?limit=${limit}` : "";
  return request<ReadingNote[]>(`${BASE_URL}/api/reading-notes${qs}`);
}

export async function deleteReadingNote(noteId: string): Promise<{ deleted: string }> {
  return request<{ deleted: string }>(`${BASE_URL}/api/reading-notes/${noteId}`, {
    method: "DELETE",
  });
}

export async function getAnnotations(targetId: string): Promise<Annotation[]> {
  return request<Annotation[]>(
    `${BASE_URL}/api/annotations?target_id=${encodeURIComponent(targetId)}`,
  );
}

export async function addAnnotation(
  targetId: string,
  content: string,
): Promise<Annotation> {
  return request<Annotation>(`${BASE_URL}/api/annotations`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ target_id: targetId, content }),
  });
}

export async function deleteAnnotation(
  annotationId: string,
): Promise<{ deleted: string }> {
  return request<{ deleted: string }>(`${BASE_URL}/api/annotations/${annotationId}`, {
    method: "DELETE",
  });
}
