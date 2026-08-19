import { useEffect, useRef } from "react";
import { postObserve } from "../api/client";
import type { Presence } from "../types/api";

// 活跃度采集 + classifyPresence 判定 + POST /api/observe（README §2，核心先行唯一被动上报）。
// 无 store：纯「采集 → 判定 → 上报」，不上屏，结果存后端 last_presence；App 层挂载一次（01-sse §6）。
const ACTIVE_WINDOW_SEC = 30;
const OBSERVE_INTERVAL_SEC = 30;

// 判定镜像后端 14-activity observe.py（规则逐字一致，不另造）
export function classifyPresence(
  keyboardActive: boolean,
  mouseActive: boolean,
  windowTitle: string,
): Presence {
  if (keyboardActive || mouseActive) return "online";
  if (windowTitle) return "busy";
  return "away";
}

export function usePresence(): void {
  const lastKeyTs = useRef(0); // 初始 0 = 「从未活跃」（now - 0 远大于活跃窗口）
  const lastMouseTs = useRef(0);
  const lastPresence = useRef<Presence | null>(null); // null → 首次采样必报

  useEffect(() => {
    const onKey = () => {
      lastKeyTs.current = Date.now();
    };
    const onMouse = () => {
      lastMouseTs.current = Date.now();
    };
    window.addEventListener("keydown", onKey);
    window.addEventListener("mousemove", onMouse);

    const sample = () => {
      const now = Date.now();
      const keyboardActive = now - lastKeyTs.current < ACTIVE_WINDOW_SEC * 1000;
      const mouseActive = now - lastMouseTs.current < ACTIVE_WINDOW_SEC * 1000;
      // 窗口标题：核心先行恒传 ""（无输入时恒走 away 分支）；Tauri getCurrentWindow().title() 后续补（README §2）
      const presence = classifyPresence(keyboardActive, mouseActive, "");
      if (presence !== lastPresence.current) {
        lastPresence.current = presence;
        // 上报 best-effort：失败只记日志，下次采样重试，不上屏（05-client §2）
        void postObserve(presence).catch((err) => {
          console.error("presence 上报失败", err);
        });
      }
    };

    sample(); // 首次挂载必报（后端 last_presence 初始 "away"，前端真实值要对齐）
    const timer = setInterval(sample, OBSERVE_INTERVAL_SEC * 1000);

    return () => {
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("mousemove", onMouse);
      clearInterval(timer);
    };
  }, []);
}
