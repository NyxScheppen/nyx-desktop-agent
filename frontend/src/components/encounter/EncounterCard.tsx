import { useEncounterStore } from "../../stores/encounterStore";
import { ENCOUNTER_KIND_LABELS } from "../../lib/labels";

// 遭遇卡片（19-encounter / design §3.5）：ENCOUNTER_START 的文本 + 可点选项。
// 读 encounterStore.current，null 不渲染；选项点击 → choose()；choosing 期间禁用。
// 开场文本与选项只在卡片内，不上聊天历史（结局经 encounter_end 上屏，见 3）。
export default function EncounterCard() {
  const current = useEncounterStore((s) => s.current);
  const choosing = useEncounterStore((s) => s.choosing);
  const error = useEncounterStore((s) => s.error);
  const choose = useEncounterStore((s) => s.choose);

  if (current === null) return null;

  return (
    <div className="encounter-card">
      <span className="encounter-card__badge">
        {ENCOUNTER_KIND_LABELS[current.kind] ?? current.kind}
      </span>
      <p className="encounter-card__text">{current.text}</p>
      <div className="encounter-card__options">
        {current.options.map((o) => (
          <button
            key={o.index}
            type="button"
            className="encounter-card__option"
            disabled={choosing}
            onClick={() => void choose(current.encounter_id, o.index)}
          >
            {o.text}
          </button>
        ))}
      </div>
      {error !== null && <p className="error-text">{error}</p>}
    </div>
  );
}
