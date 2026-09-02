import { useEffect, useRef, useState } from "react";
import type { CSSProperties, PointerEvent as ReactPointerEvent } from "react";
import AnnounceLayer from "../AnnounceLayer";
import EmotionSprite from "./EmotionSprite";
import { useAnnounceStore } from "../../stores/announceStore";
import { useChatStore } from "../../stores/chatStore";
import { useInnerLifeStore } from "../../stores/innerLifeStore";
import { useSettingsStore, type AvatarPos, type CircleSize } from "../../stores/settingsStore";
import type { EmotionCategory } from "../../types/api";

// 昼夜节律：夜间（22:00–06:00）默认困倦，白天回落当前情绪。
// 抽成纯函数便于测试（Avatar 内用 new Date().getHours() 传参）。
export function isNight(hour: number): boolean {
  return hour >= 22 || hour < 6;
}

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

// 戳立绘短语（借鉴 nyx_desktop_agent DockAvatar）：连续戳害羞，戳多了生气。
const SHY_PHRASES = ["呀！", "别戳啦……", "呜，好痒……"];
const ANGRY_PHRASES = ["不要再戳了啦！", "小狐狸我呀，要生气了！"];
const ANGRY_THRESHOLD = 5; // 连续戳 ≥5 次生气
const POKE_RESET_MS = 1500; // 停手 1.5s 后戳计数 + 临时情绪复位
const DRAG_THRESHOLD = 3; // 指针位移超过 3px 判定为拖拽（否则算戳）

// 可拖拽头像圆圈（视觉改造 §4）：白底/可换底色圆形，内放方形表情头像（expressions/）；可拖到窗口任意处（position:fixed），
// 位置/底色/尺寸存 localStorage（settingsStore）；碎碎念气泡（AnnounceLayer）头顶冒出、随圆圈走。
// 三项交互增强——
// 1) 拖拽：pointer 捕获 + 位移阈值区分「戳/拖」，拖完提交 setAvatarPos 持久化；
// 2) 戳：点击临时害羞/生气 + announce 冒一句（moved 守卫：拖拽不触发戳）；
// 3) 红点通知：搭话（initiate_chat）时挂右上角红点，点击清除。
export default function Avatar() {
  const emotion = useInnerLifeStore((s) => s.current?.emotion);
  const unreadProactive = useChatStore((s) => s.unreadProactive);
  const clearUnreadProactive = useChatStore((s) => s.clearUnreadProactive);
  const announce = useAnnounceStore((s) => s.announce);
  const circleColor = useSettingsStore((s) => s.circleColor);
  const circleSize = useSettingsStore((s) => s.circleSize);
  const avatarPos = useSettingsStore((s) => s.avatarPos);
  const setAvatarPos = useSettingsStore((s) => s.setAvatarPos);
  const size = CIRCLE_SIZES[circleSize];

  const [pokeEmotion, setPokeEmotion] = useState<EmotionCategory | null>(null);
  const pokeCount = useRef(0);
  const pokeTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  // 拖拽中途的实时坐标（渲染用）；松手时才提交进 store（一次 localStorage 写，避免 60fps 狂写）。
  const [dragPos, setDragPos] = useState<AvatarPos | null>(null);
  // 拖拽起点：指针按下时的 client 坐标 + 圆圈当时的视口 left/top（fixed 即视口坐标）。
  const dragStart = useRef<{ originX: number; originY: number; left: number; top: number } | null>(null);
  // 位移是否越过阈值（true = 拖拽，不触发戳）。
  const moved = useRef(false);

  // 挂载时 / 尺寸变化时把记忆坐标夹回当前视口（窗口或圆圈尺寸可能变小，防圆圈跑出屏幕够不到）。
  useEffect(() => {
    const pos = useSettingsStore.getState().avatarPos;
    if (pos === null) return;
    const clamped = clampAvatarPos(pos, window.innerWidth, window.innerHeight, size);
    if (clamped.x !== pos.x || clamped.y !== pos.y) {
      useSettingsStore.getState().setAvatarPos(clamped);
    }
  }, [size]);

  const handlePoke = () => {
    if (moved.current) {
      moved.current = false; // 拖拽后的 click，吞掉戳、复位标记
      return;
    }
    pokeCount.current += 1;
    if (pokeTimer.current !== null) clearTimeout(pokeTimer.current);
    pokeTimer.current = setTimeout(() => {
      pokeCount.current = 0;
      setPokeEmotion(null);
      pokeTimer.current = null;
    }, POKE_RESET_MS);

    if (pokeCount.current >= ANGRY_THRESHOLD) {
      setPokeEmotion("angry");
      announce("mutter", ANGRY_PHRASES[(pokeCount.current - ANGRY_THRESHOLD) % ANGRY_PHRASES.length]);
    } else {
      setPokeEmotion("shy");
      announce("mutter", SHY_PHRASES[(pokeCount.current - 1) % SHY_PHRASES.length]);
    }
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

  const displayed = pokeEmotion ?? (isNight(new Date().getHours()) ? "sleepy" : emotion);

  const pos = dragPos ?? avatarPos;
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
      title="戳一戳"
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={(e) => finishDrag(e.clientX, e.clientY)}
      onPointerCancel={() => finishDrag(null, null)}
      onClick={handlePoke}
    >
      <div className="avatar-circle__face">
        <EmotionSprite size="circle" emotion={displayed} />
      </div>
      <AnnounceLayer />
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
