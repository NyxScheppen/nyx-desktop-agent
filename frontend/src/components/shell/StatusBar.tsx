import { DESIRE_TYPE_LABELS, EMOTION_LABELS } from "../../lib/labels";
import { activityStatusText } from "../../lib/activityResult";
import { useActivityStore } from "../../stores/activityStore";
import { useDesireStore } from "../../stores/desireStore";
import { useInnerLifeStore } from "../../stores/innerLifeStore";
import Avatar from "../inner/Avatar";
import EnergyBar from "../inner/EnergyBar";

type StatusBarProps = {
  onOpenDetail: () => void;
};

// 精简状态条（左栏）：头像 + 心情/精力 + 当前活动 + 欲望一句话。点击信息区打开内在详情弹层。
export default function StatusBar({ onOpenDetail }: StatusBarProps) {
  const current = useInnerLifeStore((s) => s.current);
  const activity = useActivityStore((s) => s.data);
  const desires = useDesireStore((s) => s.data);

  const mood = current === null ? "…" : EMOTION_LABELS[current.emotion];
  const statusText = activityStatusText(activity?.current ?? null);
  // 欲望一句话：取最强的活短期欲望，无则长期欲望第一条，再无则空
  const desire =
    desires?.short_term.find(
      (d) => d.status !== "expired" && d.status !== "satisfied",
    ) ?? desires?.long_term[0] ?? null;
  const desireLine =
    desire === null
      ? null
      : `[${DESIRE_TYPE_LABELS[desire.type]}] ${desire.description}`;

  return (
    <div className="status-bar">
      <Avatar />
      <button type="button" className="status-bar__info" onClick={onOpenDetail}>
        <span className="status-bar__name">✦ Nyx ✦</span>
        <span className="status-bar__mood">心情：{mood}</span>
        <span className="status-bar__activity">{statusText}</span>
        {desireLine !== null && <span className="status-bar__desire">{desireLine}</span>}
      </button>
      {current !== null && (
        <EnergyBar energy={current.energy} energy_state={current.energy_state} />
      )}
    </div>
  );
}
