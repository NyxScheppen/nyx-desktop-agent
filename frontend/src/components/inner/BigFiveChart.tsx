import type { Personality } from "../../types/api";
import { PERSONALITY_POLES } from "../../lib/labels";
import BarChart from "./BarChart";

// Big Five 五维（04 §5）：双端语义经 PERSONALITY_POLES，渲染委托共享 BarChart。
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
  return <BarChart keys={BIG_FIVE_KEYS} data={personality} poles={PERSONALITY_POLES} />;
}
