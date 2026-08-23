import type { ReactNode } from "react";
import { useDraggable } from "../../hooks/useDraggable";

// 弹窗面板尺寸（居中初始位计算用，与 index.css 的 .draggable-panel 宽高一致）
// 16:9 横版，匹配显示器比例（960×540 = 1080p 半屏）
const PANEL_WIDTH = 960;
const PANEL_HEIGHT = 540;

// 初始居中位：视口减去面板尺寸各半（jsdom 默认 1024×768 下仍可测）。
function centeredPos(): { x: number; y: number } {
  const w = typeof window !== "undefined" ? window.innerWidth : 1024;
  const h = typeof window !== "undefined" ? window.innerHeight : 768;
  return {
    x: Math.max(0, Math.round((w - PANEL_WIDTH) / 2)),
    y: Math.max(0, Math.round((h - PANEL_HEIGHT) / 2)),
  };
}

type DraggablePanelProps = {
  title: string;
  onClose: () => void;
  children: ReactNode;
};

// 可拖拽弹窗骨架（借鉴 nyx_desktop_agent 的 DraggablePanel）：position:fixed 浮层，
// 头部为拖拽手柄（.drag-handle：标题 + ⋮⋮ 拖动提示 + × 关闭），主体内容由调用方注入。
export default function DraggablePanel({ title, onClose, children }: DraggablePanelProps) {
  const { pos, pointerHandlers } = useDraggable(centeredPos());

  return (
    <div className="draggable-panel" style={{ left: pos.x, top: pos.y }} {...pointerHandlers}>
      <header className="draggable-panel__header drag-handle">
        <span className="draggable-panel__title">{title}</span>
        <span className="draggable-panel__hint">⋮⋮ 拖动</span>
        <button
          type="button"
          className="draggable-panel__close"
          onClick={onClose}
          aria-label="关闭"
        >
          ×
        </button>
      </header>
      <div className="draggable-panel__body">{children}</div>
    </div>
  );
}
