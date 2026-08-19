import type { CSSProperties } from "react";

// 樱花飘落装饰（纯 CSS 动画，视觉改造 §3）：模块加载时生成一次固定片数组，
// render 稳定不重算；pointer-events: none，纯氛围、不碰数据流。
type Petal = {
  left: string;
  duration: string;
  delay: string;
  size: number;
  hue: number;
};

const PETALS: Petal[] = Array.from({ length: 24 }, (_, i) => ({
  left: `${(i * 37 + 11) % 100}%`,
  duration: `${9 + ((i * 13) % 8)}s`,
  delay: `${(i * 7) % 20}s`,
  size: 10 + ((i * 5) % 12),
  hue: 340 + ((i * 3) % 20),
}));

export default function Sakura() {
  return (
    <div className="sakura-container" aria-hidden="true">
      {PETALS.map((p, i) => (
        <span
          key={i}
          className="sakura-petal"
          style={
            {
              left: p.left,
              width: `${p.size}px`,
              height: `${p.size}px`,
              animationDuration: p.duration,
              animationDelay: p.delay,
              background: `radial-gradient(circle at 30% 30%, hsl(${p.hue}, 70%, 85%), hsl(${p.hue}, 60%, 70%))`,
            } as CSSProperties
          }
        />
      ))}
    </div>
  );
}
