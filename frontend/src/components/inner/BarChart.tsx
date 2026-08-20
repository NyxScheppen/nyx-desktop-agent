// 双端量表渲染（04 §5）：BigFiveChart / ValuesChart 共享，每维一行 = 低端词 + 滑块点 + 高端词。
// 值 1-10 映射滑块位置（(v-1)/9 → 0-100%），钳回 [0,100]（后端契约已 1-10，钳一次防瞬时越界溢出）。
// poles 提供键名 → 双端语义（PERSONALITY_POLES / VALUES_POLES），缺省回退键名原值。
type BarChartProps = {
  keys: readonly string[];
  data: Record<string, number>;
  poles: Record<string, { low: string; high: string }>;
};

export default function BarChart({ keys, data, poles }: BarChartProps) {
  return (
    <div className="bar-chart">
      {keys.map((key) => {
        const pole = poles[key] ?? { low: key, high: key };
        const pct = Math.min(100, Math.max(0, ((data[key] - 1) / 9) * 100));
        return (
          <div className="bar-chart__row" key={key}>
            <span className="bar-chart__pole bar-chart__pole--low">{pole.low}</span>
            <div className="bar-chart__track">
              <span className="bar-chart__dot" style={{ left: `${pct}%` }} />
            </div>
            <span className="bar-chart__pole bar-chart__pole--high">{pole.high}</span>
          </div>
        );
      })}
    </div>
  );
}
