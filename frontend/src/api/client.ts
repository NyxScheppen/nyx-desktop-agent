import type { CurrentState, Presence } from "../types/api";

// 与后端 uvicorn 启动参数一致（frontend/README §1）
export const BASE_URL = "http://localhost:8000";

// 统一错误契约（05-client §2）：成功返回数据、失败 throw，不包裹 {ok, data}。
// fetch 网络错误（TypeError）自然上抛不吞；非 2xx 读 body.detail ?? body.error ?? JSON.stringify(body)，
// 兜底保证 Error.message 非空。
async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, init);
  if (!res.ok) {
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

export async function postObserve(presence: Presence): Promise<{ event_id: string }> {
  return request<{ event_id: string }>(`${BASE_URL}/api/observe`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ presence }),
  });
}
