import { EMOTION_LABELS, DESIRE_TYPE_LABELS } from "../../lib/labels";
import { activityStatusText } from "../../lib/activityResult";
import { useInnerLifeStore } from "../../stores/innerLifeStore";
import { useDesireStore } from "../../stores/desireStore";
import { useActivityStore } from "../../stores/activityStore";
import Avatar from "../inner/Avatar";
import EnergyBar from "../inner/EnergyBar";

type LeftPanelProps = {
  onOpenInner: (categoryIndex: number) => void; // 点摘要 → 弹对应分类详情（复用 InnerWorld）
};

// 左面板（design §5.1，30%）：大头照 + 姓名 + 心情/精力（纯展示）+ 欲望一句话 + 活动一条。
// 内心世界入口一一对应：她现在的念头→内在(0)、正在做什么→记录(2)。
// 空间(1)/记录(2)/出门/游戏设置入口在右底工具条（RightDock），左面板只留内在(0)/记录(2)两条摘要直达。
export default function LeftPanel({ onOpenInner }: LeftPanelProps) {
  const current = useInnerLifeStore((s) => s.current);
  const desires = useDesireStore((s) => s.data);
  const activity = useActivityStore((s) => s.data?.current ?? null);

  // 「她现在的念头」：取最活跃的一条短期欲望（active 优先，否则按 strength 降序第一条）
  const activeDesire =
    desires?.short_term.find((d) => d.status === "active") ??
    desires?.short_term[0];

  return (
    <aside className="left-panel">
      <Avatar />
      <h2 className="left-panel__name">Nyx</h2>

      <div className="left-panel__summary left-panel__summary--static">
        <div className="left-panel__summary-line">
          <span className="left-panel__summary-label">心情</span>
          <span className="left-panel__summary-value">
            {current !== null ? EMOTION_LABELS[current.emotion] : "……"}
          </span>
        </div>
        {current !== null && (
          <EnergyBar energy={current.energy} energy_state={current.energy_state} />
        )}
      </div>

      <button
        type="button"
        className="left-panel__summary"
        onClick={() => onOpenInner(0)} // 内在分类（内在状态/欲望/叙事）
      >
        <span className="left-panel__summary-label">她现在的念头</span>
        <span className="left-panel__summary-value">
          {activeDesire !== undefined
            ? `${DESIRE_TYPE_LABELS[activeDesire.type]} · ${activeDesire.description}`
            : "此刻没有特别的念头"}
        </span>
      </button>

      <button
        type="button"
        className="left-panel__summary"
        onClick={() => onOpenInner(2)} // 记录分类（活动/记忆）
      >
        <span className="left-panel__summary-label">正在做什么</span>
        <span className="left-panel__summary-value">
          {activityStatusText(activity)}
        </span>
      </button>
    </aside>
  );
}
