// 中间内容区当前视图（聊天不再可切换，左栏常驻）：默认 reading。
export type View = "inner" | "desire" | "activity" | "memory" | "reading";

type RightDockProps = {
  view: View; // 当前视图，用于高亮激活入口
  onSwitch: (view: View) => void;
  onOpenSettings: () => void; // 打开设置弹层（设置入口从顶栏迁到底部导航）
};

// 底部工具条（中间内容区下方，常驻不随切视图消失）：内在状态 / 欲望 / 活动 / 记忆 / 读书 + 设置。
// 五个入口切换中间内容区（切视图），当前入口高亮；「设置」单独按钮开设置弹层。未来加词条只需往 ENTRIES 追加一项。
const ENTRIES: readonly { label: string; view: View }[] = [
  { label: "内在状态", view: "inner" },
  { label: "欲望", view: "desire" },
  { label: "活动", view: "activity" },
  { label: "记忆", view: "memory" },
  { label: "读书", view: "reading" },
];

export default function RightDock({ view, onSwitch, onOpenSettings }: RightDockProps) {
  return (
    <div className="right-dock">
      {ENTRIES.map((e) => (
        <button
          key={e.label}
          type="button"
          className={`right-dock__entry${
            view === e.view ? " right-dock__entry--active" : ""
          }`}
          aria-pressed={view === e.view}
          onClick={() => onSwitch(e.view)}
        >
          {e.label}
        </button>
      ))}
      <button
        type="button"
        className="right-dock__entry right-dock__entry--settings"
        onClick={onOpenSettings}
      >
        设置
      </button>
    </div>
  );
}
