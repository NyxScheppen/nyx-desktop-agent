import type { Values } from "../../types/api";

// 三观四维条形（04 §5）：同款条形（与 BigFiveChart 共享 .bar-chart 样式），标签 snake_case 原值。
const VALUES_KEYS: readonly (keyof Values)[] = [
  "attitude_to_human",
  "ai_identity_acceptance",
  "altruism",
  "optimism",
];

type ValuesChartProps = {
  values: Values;
};

export default function ValuesChart({ values }: ValuesChartProps) {
  return (
    <div className="bar-chart">
      {VALUES_KEYS.map((key) => (
        <div className="bar-chart__row" key={key}>
          <span className="bar-chart__label">{key}</span>
          <div className="bar-chart__track">
            <div
              className="bar-chart__fill"
              style={{ width: `${Math.min(100, Math.max(0, values[key] * 10))}%` }}
            />
          </div>
          <span className="bar-chart__value">{values[key]}</span>
        </div>
      ))}
    </div>
  );
}
