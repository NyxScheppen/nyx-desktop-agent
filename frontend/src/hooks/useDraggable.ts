import { useRef, useState, type PointerEvent as ReactPointerEvent } from "react";

// 可拖拽弹窗 hook（借鉴 nyx_desktop_agent 的 useDraggable）：pointer 事件拖拽，
// 手柄限定 .drag-handle（非手柄 / 按钮上按下不拖），边界 clamp（x 允许 -100 部分滑出，y 不越顶）。
// pos 为绝对像素坐标（left/top），由 DraggablePanel 内联样式消费。
export type Position = { x: number; y: number };

export function useDraggable(initial: Position): {
  pos: Position;
  resetPosition: () => void;
  pointerHandlers: {
    onPointerDown: (e: ReactPointerEvent<HTMLDivElement>) => void;
    onPointerMove: (e: ReactPointerEvent<HTMLDivElement>) => void;
    onPointerUp: () => void;
  };
} {
  const [pos, setPos] = useState<Position>(initial);
  const start = useRef<{ x: number; y: number; px: number; py: number } | null>(null);

  const onPointerDown = (e: ReactPointerEvent<HTMLDivElement>) => {
    const target = e.target as HTMLElement;
    if (!target.closest(".drag-handle")) return;
    if (target.closest("button")) return;
    start.current = { x: e.clientX, y: e.clientY, px: pos.x, py: pos.y };
    // pointer capture：拖出面板后仍收 move；jsdom 未实现则跳过（测试不崩）
    if (typeof e.currentTarget.setPointerCapture === "function") {
      e.currentTarget.setPointerCapture(e.pointerId);
    }
  };

  const onPointerMove = (e: ReactPointerEvent<HTMLDivElement>) => {
    if (start.current === null) return;
    const dx = e.clientX - start.current.x;
    const dy = e.clientY - start.current.y;
    setPos({
      x: Math.max(-100, start.current.px + dx),
      y: Math.max(0, start.current.py + dy),
    });
  };

  const onPointerUp = () => {
    start.current = null;
  };

  return {
    pos,
    resetPosition: () => setPos(initial),
    pointerHandlers: { onPointerDown, onPointerMove, onPointerUp },
  };
}
