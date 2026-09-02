import type { Aesthetic } from "../../types/api";
import { AESTHETIC_POLES } from "../../lib/labels";
import BarChart from "./BarChart";

// 审美四轴（04 §5）：双端语义经 AESTHETIC_POLES，渲染委托共享 BarChart。
const AESTHETIC_KEYS: readonly (keyof Aesthetic)[] = [
  "ornate",
  "lyrical",
  "classical",
  "somber",
];

type AestheticChartProps = {
  aesthetic: Aesthetic;
};

export default function AestheticChart({ aesthetic }: AestheticChartProps) {
  return <BarChart keys={AESTHETIC_KEYS} data={aesthetic} poles={AESTHETIC_POLES} />;
}
