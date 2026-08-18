import type { CurrentState, Presence } from "../types/api";

// 与后端 uvicorn 启动参数一致（frontend/README §1）
export const BASE_URL = "http://localhost:8000";

// 占位：fetch 封装 + 统一错误契约在 05-client 实现阶段填充
export async function postChat(_message: string): Promise<{ event_id: string }> {
  throw new Error("not implemented");
}

export async function getState(): Promise<CurrentState> {
  throw new Error("not implemented");
}

export async function postObserve(
  _presence: Presence
): Promise<{ event_id: string }> {
  throw new Error("not implemented");
}
