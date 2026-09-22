import { useEffect, useRef, useState, type ReactNode } from "react";
import type { CSSProperties, PointerEvent as ReactPointerEvent } from "react";
import AnnounceLayer from "../AnnounceLayer";
import { isTauriRuntime, startNativeWindowDrag } from "../../lib/desktopWindow";
import EmotionSprite from "./EmotionSprite";
import { useChatStore } from "../../stores/chatStore";
import { useInnerLifeStore } from "../../stores/innerLifeStore";
import { useSettingsStore, type AvatarPos, type CircleSize } from "../../stores/settingsStore";

type AvatarProps = {
  night: boolean;
  children?: ReactNode;
  showAnnouncements?: boolean;
  useNativeWindowDrag?: boolean;
  onActivate?: () => void;
  onDoubleClick?: () => void;
};

// 头像圆圈三档直径（px）：小/中/大。供 clampAvatarPos 边界夹取 + 内联 width/height。
export const CIRCLE_SIZES: Record<CircleSize, number> = {
  small: 96,
  medium: 120,
  large: 144,
};

/** 纯函数：把拖拽坐标夹回视口内（0..viewport-size），越界回弹；视口小于圆圈时钉到 0。 */
export function clampAvatarPos(
  pos: AvatarPos,
  viewportWidth: number,
  viewportHeight: number,
  size: number,
): AvatarPos {
  return {
    x: Math.min(Math.max(pos.x, 0), Math.max(viewportWidth - size, 0)),
    y: Math.min(Math.max(pos.y, 0), Math.max(viewportHeight - size, 0)),
  };
}

const DRAG_THRESHOLD = 3; // 指针位移超过 3px 判定为拖拽（否则算戳）

// 可拖拽头像圆圈（视觉改造 §4）：白底/可换底色圆形，内放方形表情头像（expressions/）；浏览器里可拖到窗口任意处（position:fixed），
// 位置/底色/尺寸存 localStorage（settingsStore）；Tauri 桌宠里拖拽的是整个原生窗口，圆球不再在窗口内单独定位；
// 碎碎念气泡（AnnounceLayer）头顶冒出、随圆圈走。桌宠模式由 onActivate/onDoubleClick 交给上层处理：单击打开菜单，双击切换完整端；
// 红点通知仍由 Avatar 负责清除。
export default function Avatar({
  night,
  children,
  showAnnouncements = true,
  useNativeWindowDrag = false,
  onActivate,
  onDoubleClick,
}: AvatarProps) {
  const emotion = useInnerLifeStore((s) => s.current?.emotion);
  const unreadProactive = useChatStore((s) => s.unreadProactive);
  const clearUnreadProactive = useChatStore((s) => s.clearUnreadProactive);
  const circleColor = useSettingsStore((s) => s.circleColor);
  const circleSize = useSettingsStore((s) => s.circleSize);
  const avatarPos = useSettingsStore((s) => s.avatarPos);
  const setAvatarPos = useSettingsStore((s) => s.setAvatarPos);
  const size = CIRCLE_SIZES[circleSize];
  // 原生窗口拖动只属于桌宠态；完整端仍在窗口内移动头像，避免拖动头像时移动整个窗口。
  const nativeWindowDrag = useNativeWindowDrag && isTauriRuntime();

  // 拖拽中途的实时坐标（渲染用）；松手时才提交进 store（一次 localStorage 写，避免 60fps 狂写）。
  const [dragPos, setDragPos] = useState<AvatarPos | null>(null);
  // 拖拽起点：指针按下时的 client 坐标 + 圆圈当时的视口 left/top（fixed 即视口坐标）。
  const dragStart = useRef<{ originX: number; originY: number; left: number; top: number } | null>(null);
  // 位移是否越过阈值（true = 拖拽，不触发戳）。
  const moved = useRef(false);

  // 挂载时 / 尺寸变化时把记忆坐标夹回当前视口（窗口或圆圈尺寸可能变小，防圆圈跑出屏幕够不到）。
  useEffect(() => {
    if (nativeWindowDrag) return;
    const pos = useSettingsStore.getState().avatarPos;
    if (pos === null) return;
    const clamped = clampAvatarPos(pos, window.innerWidth, window.innerHeight, size);
    if (clamped.x !== pos.x || clamped.y !== pos.y) {
      useSettingsStore.getState().setAvatarPos(clamped);
    }
  }, [nativeWindowDrag, size]);

  const handleActivate = () => {
    if (moved.current) {
      moved.current = false;
      return;
    }
    onActivate?.();
  };

  const onPointerDown = (e: ReactPointerEvent<HTMLDivElement>) => {
    moved.current = false;
    const rect = e.currentTarget.getBoundingClientRect();
    dragStart.current = { originX: e.clientX, originY: e.clientY, left: rect.left, top: rect.top };
    e.currentTarget.setPointerCapture(e.pointerId);
  };

  const onPointerMove = (e: ReactPointerEvent<HTMLDivElement>) => {
    const start = dragStart.current;
    if (start === null) return;
    const dx = e.clientX - start.originX;
    const dy = e.clientY - start.originY;
    if (!moved.current && Math.abs(dx) < DRAG_THRESHOLD && Math.abs(dy) < DRAG_THRESHOLD) return;
    moved.current = true;
    if (nativeWindowDrag) {
      // 点击先保留给 onClick；只有越过阈值后才交给原生窗口拖动。
      // 释放 pointer capture 后再调用 startDragging，避免 WebView 抢走系统拖动。
      if (e.currentTarget.hasPointerCapture(e.pointerId)) {
        e.currentTarget.releasePointerCapture(e.pointerId);
      }
      dragStart.current = null;
      void startNativeWindowDrag();
      return;
    }
    setDragPos(
      clampAvatarPos(
        { x: start.left + dx, y: start.top + dy },
        window.innerWidth,
        window.innerHeight,
        size,
      ),
    );
  };

  const finishDrag = (clientX: number | null, clientY: number | null) => {
    const start = dragStart.current;
    dragStart.current = null;
    setDragPos(null);
    if (nativeWindowDrag) {
      if (start !== null && clientX !== null && clientY !== null) {
        moved.current = moved.current ||
          Math.abs(clientX - start.originX) >= DRAG_THRESHOLD ||
          Math.abs(clientY - start.originY) >= DRAG_THRESHOLD;
      }
      return;
    }
    // 松手提交（pointerup）；取消（pointercancel）不提交、放弃本次拖拽。
    if (start !== null && moved.current && clientX !== null && clientY !== null) {
      setAvatarPos(
        clampAvatarPos(
          { x: start.left + (clientX - start.originX), y: start.top + (clientY - start.originY) },
          window.innerWidth,
          window.innerHeight,
          size,
        ),
      );
    }
  };

  const displayed = night ? "sleepy" : emotion;

  const pos = nativeWindowDrag ? null : dragPos ?? avatarPos;
  const style: CSSProperties = { backgroundColor: circleColor, width: size, height: size };
  if (pos !== null) {
    style.left = pos.x;
    style.top = pos.y;
    style.right = "auto";
    style.bottom = "auto";
  }

  return (
    <div
      className="avatar-circle"
      style={style}
      title="Nyx 头像"
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={(e) => finishDrag(e.clientX, e.clientY)}
      onPointerCancel={() => finishDrag(null, null)}
      onClick={handleActivate}
      onDoubleClick={onDoubleClick}
    >
      {children}
      <div className="avatar-circle__face">
        <EmotionSprite size="circle" emotion={displayed} />
      </div>
      {showAnnouncements && <AnnounceLayer />}
      {unreadProactive && (
        <button
          type="button"
          className="avatar-notice"
          aria-label="小狐狸我有话对你说"
          onPointerDown={(e) => e.stopPropagation()}
          onClick={(e) => {
            e.stopPropagation();
            clearUnreadProactive();
          }}
        />
      )}
    </div>
  );
}
