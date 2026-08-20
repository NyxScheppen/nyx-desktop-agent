import type { Values } from "../../types/api";
import { VALUES_POLES } from "../../lib/labels";
import BarChart from "./BarChart";

// 三观四维（04 §5）：双端语义经 VALUES_POLES，渲染委托共享 BarChart。
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
  return <BarChart keys={VALUES_KEYS} data={values} poles={VALUES_POLES} />;
}
