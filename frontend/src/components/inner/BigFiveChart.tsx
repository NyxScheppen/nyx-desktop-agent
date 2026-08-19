import type { Personality } from "../../types/api";

// Big Five 五维条形（04 §5）：标签用 snake_case 键名原值（不转中文，反冗余），值 1-10 映射条宽。
// 慢变量，无高频事件，只在 refreshState 全量刷新时重绘（02-stores §2）。
const BIG_FIVE_KEYS: readonly (keyof Personality)[] = [
  "openness",
  "conscientiousness",
  "extraversion",
  "agreeableness",
  "neuroticism",
];

type BigFiveChartProps = {
  personality: Personality;
};

export default function BigFiveChart({ personality }: BigFiveChartProps) {
  return (
    <div className="bar-chart">
      {BIG_FIVE_KEYS.map((key) => (
        <div className="bar-chart__row" key={key}>
          <span className="bar-chart__label">{key}</span>
          <div className="bar-chart__track">
            <div
              className="bar-chart__fill"
              style={{ width: `${Math.min(100, Math.max(0, personality[key] * 10))}%` }}
            />
          </div>
          <span className="bar-chart__value">{personality[key]}</span>
        </div>
      ))}
    </div>
  );
}
