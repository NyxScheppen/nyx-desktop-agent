// 共享条形渲染（04 §5）：BigFiveChart / ValuesChart 同款条形，收 keys + data 去重渲染逻辑与越界钳制。
// 值 1-10 映射条宽（×10 → 百分比），钳回 [0,100]（后端契约已 1-10，钳一次防瞬时越界溢出）。
type BarChartProps = {
  keys: readonly string[];
  data: Record<string, number>;
};

export default function BarChart({ keys, data }: BarChartProps) {
  return (
    <div className="bar-chart">
      {keys.map((key) => (
        <div className="bar-chart__row" key={key}>
          <span className="bar-chart__label">{key}</span>
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
