// 二维散点：x=valence[-1,1]（左负右正）、y=arousal[0,1]（下低上高），单点 + 十字虚线标出当前值
// （04-inner-state-panel §3）。轻量 SVG 手绘，不引图表库；坐标/像素不做测试断言（frontend/README §6）。
// 区域标签对齐后端 nyx/inner_life/emotion.py 的 vad_to_category 6 档（sleepy/thinking 是 resolve_emotion
// 覆盖，不进象限）：开心/生气/担忧/悲伤/害羞/平静。标签质心与散点共用同一 (v,a)→(x,y) 映射，几何一致。
import { EMOTION_LABELS } from "../../lib/labels";

const W = 220;
const H = 160;
const PAD = 24;

type ValenceArousalPlotProps = {
  valence: number;
  arousal: number;
};

// (valence, arousal) → SVG 坐标；valence/arousal 越界钳回绘图区
// （后端契约已是 [-1,1]/[0,1]，钳一次防瞬时越界点飞出画面）。
function coord(v: number, a: number): { x: number; y: number } {
  const vx = Math.min(1, Math.max(-1, v));
  const ay = Math.min(1, Math.max(0, a));
  return {
    x: PAD + ((vx + 1) / 2) * (W - 2 * PAD),
    y: PAD + (1 - ay) * (H - 2 * PAD), // SVG y 轴向下，arousal 高在上 → 取反
  };
}

// 6 档区域质心（阈值 _V_NEAR=0.2 / _A_LOW=0.3 / _A_HIGH=0.6 的几何中心）。
// 文字锚点按左右/中带分，落在各区域质心旁，不压散点。
type RegionLabel = { text: string; v: number; a: number; anchor: "start" | "middle" | "end" };

const REGION_LABELS: RegionLabel[] = [
  { text: EMOTION_LABELS.angry, v: -0.6, a: 0.8, anchor: "start" },
  { text: EMOTION_LABELS.worried, v: -0.6, a: 0.45, anchor: "start" },
  { text: EMOTION_LABELS.sad, v: -0.6, a: 0.15, anchor: "start" },
  { text: EMOTION_LABELS.happy, v: 0.6, a: 0.65, anchor: "end" },
  { text: EMOTION_LABELS.shy, v: 0.6, a: 0.15, anchor: "end" },
  { text: EMOTION_LABELS.neutral, v: 0, a: 0.5, anchor: "middle" },
];

export default function ValenceArousalPlot({ valence, arousal }: ValenceArousalPlotProps) {
  const p = coord(valence, arousal);

  return (
    <svg
      className="va-plot"
      viewBox={`0 0 ${W} ${H}`}
      role="img"
      aria-label={`valence ${valence} arousal ${arousal}`}
    >
      <rect x={PAD} y={PAD} width={W - 2 * PAD} height={H - 2 * PAD} className="va-plot__frame" />
      {/* 十字虚线：贯穿绘图区，交点为当前值 */}
      <line x1={p.x} y1={PAD} x2={p.x} y2={H - PAD} className="va-plot__cross" />
      <line x1={PAD} y1={p.y} x2={W - PAD} y2={p.y} className="va-plot__cross" />
      <circle cx={p.x} cy={p.y} r={4} className="va-plot__point" />
      {REGION_LABELS.map((l) => {
        const c = coord(l.v, l.a);
        const dx = l.anchor === "start" ? 5 : l.anchor === "end" ? -5 : 0;
        return (
          <text
            key={l.text}
            x={c.x + dx}
            y={c.y + 4}
            textAnchor={l.anchor}
            className="va-plot__label"
          >
            {l.text}
          </text>
        );
      })}
    </svg>
  );
}
