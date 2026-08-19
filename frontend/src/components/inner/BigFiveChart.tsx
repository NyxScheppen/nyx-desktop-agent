import type { Personality } from "../../types/api";
import BarChart from "./BarChart";

// Big Five 五维（04 §5）：标签 snake_case 键名原值（不转中文，反冗余），渲染委托共享 BarChart。
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
  return <BarChart keys={BIG_FIVE_KEYS} data={personality} />;
}
