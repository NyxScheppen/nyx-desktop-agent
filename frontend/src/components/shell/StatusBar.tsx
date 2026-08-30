import { EMOTION_LABELS } from "../../lib/labels";
import { activityStatusText } from "../../lib/activityResult";
import { useActivityStore } from "../../stores/activityStore";
import { useInnerLifeStore } from "../../stores/innerLifeStore";
import Avatar from "../inner/Avatar";
import EnergyBar from "../inner/EnergyBar";

// 状态条（左栏）：立绘半身像（3:4）+ 底部信息块触底（名字/心情/精力条/现在状态）。
export default function StatusBar() {
  const current = useInnerLifeStore((s) => s.current);
  const activity = useActivityStore((s) => s.data);

  const mood = current === null ? "…" : EMOTION_LABELS[current.emotion];
  const statusText = activityStatusText(activity?.current ?? null);

  return (
    <div className="status-bar">
      <Avatar />
      <div className="status-bar__info">
        <span className="status-bar__name">✦ Nyx ✦</span>
        <span className="status-bar__mood">心情：{mood}</span>
        {current !== null && (
          <EnergyBar energy={current.energy} energy_state={current.energy_state} />
        )}
        <span className="status-bar__activity">现在状态：{statusText}</span>
      </div>
    </div>
  );
}
