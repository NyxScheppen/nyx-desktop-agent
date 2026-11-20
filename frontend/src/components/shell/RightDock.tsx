import frontendPolicy from "../../../../nyx/settings/frontend.json";
export type View =
  | "chat"
  | "inner"
  | "desire"
  | "activity"
  | "task-compose"
  | "tasks"
  | "creation"
  | "memory"
  | "behavior"
  | "eval"
  | "history"
  | "settings"
  | "character-create";

type RightDockProps = {
  view: View; // 当前视图，用于高亮激活入口
  onSwitch: (view: View) => void;
  onOpenSettings: () => void; // 打开设置页面
  onReturnToPet?: () => void;
};

const ENTRIES: readonly { label: string; icon: string; view: View }[] = frontendPolicy.frontend.RightDock.ENTRIES as readonly { label: string; icon: string; view: View }[];

export default function RightDock({ view, onSwitch, onOpenSettings, onReturnToPet }: RightDockProps) {
  return (
    <div className="right-dock">
      <div className="right-dock__brand" aria-hidden="true">N</div>
      {ENTRIES.map((e) => (
        <button
          key={e.label}
          type="button"
          className={`right-dock__entry${
            view === e.view ? " right-dock__entry--active" : ""
          }`}
          aria-pressed={view === e.view}
          aria-label={e.label}
          title={e.label}
          onClick={() => onSwitch(e.view)}
        >
          <span aria-hidden="true">{e.icon}</span>
        </button>
      ))}
      <button
        type="button"
        className={`right-dock__entry right-dock__entry--settings${(view === "settings" || view === "character-create") ? " right-dock__entry--active" : ""}`}
        aria-pressed={(view === "settings" || view === "character-create")}
        aria-label={frontendPolicy.copy.RightDock.aria_label}
        title={frontendPolicy.copy.RightDock.aria_label}
        onClick={onOpenSettings}
      >
        <span aria-hidden="true">⚙</span>
      </button>
      {onReturnToPet ? (
        <button type="button" className="right-dock__seal" aria-label={frontendPolicy.copy.RightDock.aria_label_2} title={frontendPolicy.copy.RightDock.aria_label_2} onClick={onReturnToPet}>✣</button>
      ) : <div className="right-dock__seal" aria-hidden="true">✣</div>}
    </div>
  );
}
