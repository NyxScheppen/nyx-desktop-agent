import type { Values } from "../../types/api";
import { VALUES_LABELS } from "../../lib/labels";
import BarChart from "./BarChart";

// 三观四维（04 §5）：同款条形，标签经 VALUES_LABELS 转中文，渲染委托共享 BarChart。
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
  return <BarChart keys={VALUES_KEYS} data={values} labels={VALUES_LABELS} />;
}
