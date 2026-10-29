export type View =
  | "chat"
  | "inner"
  | "desire"
  | "activity"
  | "task-compose"
  | "tasks"
  | "creation"
  | "memory"
  | "eval"
  | "history"
  | "settings";

type RightDockProps = {
  view: View; // 当前视图，用于高亮激活入口
  onSwitch: (view: View) => void;
  onOpenSettings: () => void; // 打开设置页面
  onReturnToPet?: () => void;
};

const ENTRIES: readonly { label: string; icon: string; view: View }[] = [
  { label: "聊天", icon: "✦", view: "chat" },
  { label: "内在状态", icon: "◌", view: "inner" },
  { label: "欲望", icon: "◇", view: "desire" },
  { label: "活动", icon: "◷", view: "activity" },
  { label: "任务", icon: "▣", view: "tasks" },
  { label: "创作", icon: "✎", view: "creation" },
  { label: "记忆", icon: "▤", view: "memory" },
  { label: "评估", icon: "⌁", view: "eval" },
  { label: "历史", icon: "↺", view: "history" },
];

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
        className={`right-dock__entry right-dock__entry--settings${view === "settings" ? " right-dock__entry--active" : ""}`}
        aria-pressed={view === "settings"}
        aria-label="设置"
        title="设置"
        onClick={onOpenSettings}
      >
        <span aria-hidden="true">⚙</span>
      </button>
      {onReturnToPet ? (
        <button type="button" className="right-dock__seal" aria-label="收回桌宠" title="收回桌宠" onClick={onReturnToPet}>✣</button>
      ) : <div className="right-dock__seal" aria-hidden="true">✣</div>}
    </div>
  );
}
