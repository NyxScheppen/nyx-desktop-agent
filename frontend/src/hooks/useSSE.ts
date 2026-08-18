import type { ConnectionState, SseEvent } from "../types/api";

// 占位：EventSource 订阅 + 帧解析 + 连接状态机在 01-sse 实现阶段填充
export function useSSE(_dispatch: (e: SseEvent) => void): ConnectionState {
  return "connecting";
}
