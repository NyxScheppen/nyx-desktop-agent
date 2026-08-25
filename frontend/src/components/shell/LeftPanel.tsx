import { useState } from "react";
import { EMOTION_LABELS, DESIRE_TYPE_LABELS } from "../../lib/labels";
import { activityStatusText } from "../../lib/activityResult";
import { useInnerLifeStore } from "../../stores/innerLifeStore";
import { useDesireStore } from "../../stores/desireStore";
import { useActivityStore } from "../../stores/activityStore";
import { useSettingsStore } from "../../stores/settingsStore";
import Avatar from "../inner/Avatar";
import EnergyBar from "../inner/EnergyBar";

type LeftPanelProps = {
  onOpenInner: (categoryIndex: number) => void; // 点摘要 → 弹对应分类详情（复用 InnerWorld）
};

// 左面板（design §5.1，25%）：大头照 + 姓名 + 属性摘要（情绪/精力）+ 欲望一句话
// + 活动一条 + 游戏设置（背景/字体大小）。点摘要 → onOpenInner 弹详情。
export default function LeftPanel({ onOpenInner }: LeftPanelProps) {
  const current = useInnerLifeStore((s) => s.current);
  const desires = useDesireStore((s) => s.data);
  const activity = useActivityStore((s) => s.data?.current ?? null);
  const fontScale = useSettingsStore((s) => s.fontScale);
  const setFontScale = useSettingsStore((s) => s.setFontScale);
  const [settingsOpen, setSettingsOpen] = useState(false);

  // 「她现在的念头」：取最活跃的一条短期欲望（active 优先，否则按 strength 降序第一条）
  const activeDesire =
    desires?.short_term.find((d) => d.status === "active") ??
    desires?.short_term[0];

  return (
    <aside className="left-panel">
      <Avatar />
      <h2 className="left-panel__name">Nyx</h2>

      <button
        type="button"
        className="left-panel__summary"
        onClick={() => onOpenInner(0)} // 内在分类（内在状态/欲望/叙事）
      >
        <span className="left-panel__summary-label">心情</span>
        <span className="left-panel__summary-value">
          {current !== null ? EMOTION_LABELS[current.emotion] : "……"}
        </span>
        {current !== null && (
          <EnergyBar energy={current.energy} energy_state={current.energy_state} />
        )}
      </button>

      <button
        type="button"
        className="left-panel__summary"
        onClick={() => onOpenInner(0)}
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

      <div className="left-panel__settings">
        <button
          type="button"
          className="left-panel__settings-toggle"
          aria-expanded={settingsOpen}
          onClick={() => setSettingsOpen((v) => !v)}
        >
          游戏设置
        </button>
        {settingsOpen && (
          <div className="left-panel__settings-body">
            <span className="left-panel__settings-label">字体大小</span>
            <div className="left-panel__font">
              {(
                [
                  ["small", "小"],
                  ["medium", "中"],
                  ["large", "大"],
                ] as const
              ).map(([key, label]) => (
                <button
                  key={key}
                  type="button"
                  className={`left-panel__font-opt${fontScale === key ? " left-panel__font-opt--active" : ""}`}
                  aria-pressed={fontScale === key}
                  onClick={() => setFontScale(key)}
                >
                  {label}
                </button>
              ))}
            </div>
          </div>
        )}
      </div>
    </aside>
  );
}
