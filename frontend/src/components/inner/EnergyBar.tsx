import type { EnergyState } from "../../types/api";

// 精力条（04 §4）：横向进度条 energy∈[0,100]；文案显示 energy_state 枚举值原值（不转中文，反冗余）。
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
      <span className="energy-bar__label">{energy_state}</span>
    </div>
  );
}
