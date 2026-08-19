// 二维散点：x=valence[-1,1]（左负右正）、y=arousal[0,1]（下低上高），单点 + 十字虚线标出当前值
// （04-inner-state-panel §3）。轻量 SVG 手绘，不引图表库；坐标/像素不做测试断言（frontend/README §6）。
const W = 220;
const H = 160;
const PAD = 24;

type ValenceArousalPlotProps = {
  valence: number;
  arousal: number;
};

export default function ValenceArousalPlot({ valence, arousal }: ValenceArousalPlotProps) {
  // 越界钳回绘图区（后端契约已是 [-1,1]/[0,1]，钳一次防瞬时越界点飞出画面）
  const vx = Math.min(1, Math.max(-1, valence));
  const vy = Math.min(1, Math.max(0, arousal));
  const x = PAD + ((vx + 1) / 2) * (W - 2 * PAD);
  const y = PAD + (1 - vy) * (H - 2 * PAD); // SVG y 轴向下，arousal 高在上 → 取反

  return (
    <svg
      className="va-plot"
      viewBox={`0 0 ${W} ${H}`}
      role="img"
      aria-label={`valence ${valence} arousal ${arousal}`}
    >
      <rect x={PAD} y={PAD} width={W - 2 * PAD} height={H - 2 * PAD} className="va-plot__frame" />
      {/* 十字虚线：贯穿绘图区，交点为当前值 */}
      <line x1={x} y1={PAD} x2={x} y2={H - PAD} className="va-plot__cross" />
      <line x1={PAD} y1={y} x2={W - PAD} y2={y} className="va-plot__cross" />
      <circle cx={x} cy={y} r={4} className="va-plot__point" />
      {/* 象限轻标注（纯视觉辅助）：右上高兴、右下平静、左上愤怒、左下低落 */}
      <text x={W - PAD - 4} y={PAD + 12} textAnchor="end" className="va-plot__label">
        高兴
      </text>
      <text x={W - PAD - 4} y={H - PAD - 4} textAnchor="end" className="va-plot__label">
        平静
      </text>
      <text x={PAD + 4} y={PAD + 12} className="va-plot__label">
        愤怒
      </text>
      <text x={PAD + 4} y={H - PAD - 4} className="va-plot__label">
        低落
      </text>
    </svg>
  );
}
