import type { EnergyState } from "../../types/api";
import { ENERGY_LABELS } from "../../lib/labels";

// 精力条（04 §4）：横向进度条 energy∈[0,100]；文案经 ENERGY_LABELS 转中文。
// 颜色按 energy_state 分段（绿→黄→红），纯视觉，不做测试断言。
const ENERGY_COLOR: Record<EnergyState, string> = {
  energetic: "#3fa34d",
  okay: "#a3be3f",
  tired: "#e0b000",
  exhausted: "#e07b00",
  drained: "#c0392b",
};

type EnergyBarProps = {
  energy: number;
  energy_state: EnergyState;
};

export default function EnergyBar({ energy, energy_state }: EnergyBarProps) {
  const pct = Math.min(100, Math.max(0, energy));
  return (
    <div className="energy-bar">
      <div className="energy-bar__track">
        <div
          className="energy-bar__fill"
          style={{ width: `${pct}%`, backgroundColor: ENERGY_COLOR[energy_state] }}
        />
      </div>
      <span className="energy-bar__label">{ENERGY_LABELS[energy_state]}</span>
    </div>
  );
}
