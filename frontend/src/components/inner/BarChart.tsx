// 共享条形渲染（04 §5）：BigFiveChart / ValuesChart 同款条形，收 keys + data 去重渲染逻辑与越界钳制。
// 值 1-10 映射条宽（×10 → 百分比），钳回 [0,100]（后端契约已 1-10，钳一次防瞬时越界溢出）。
// labels 提供键名 → 中文（PERSONALITY_LABELS / VALUES_LABELS），缺省回退键名原值。
type BarChartProps = {
  keys: readonly string[];
  data: Record<string, number>;
  labels?: Record<string, string>;
};

export default function BarChart({ keys, data, labels }: BarChartProps) {
  return (
    <div className="bar-chart">
      {keys.map((key) => (
        <div className="bar-chart__row" key={key}>
          <span className="bar-chart__label">{labels?.[key] ?? key}</span>
          <div className="bar-chart__track">
            <div
              className="bar-chart__fill"
              style={{ width: `${Math.min(100, Math.max(0, data[key] * 10))}%` }}
            />
          </div>
          <span className="bar-chart__value">{data[key]}</span>
        </div>
      ))}
    </div>
  );
}
