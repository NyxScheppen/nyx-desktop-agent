// 中间内容区当前视图（聊天不再可切换，左栏常驻）：默认 reading。
export type View = "inner" | "desire" | "activity" | "memory" | "reading";

type RightDockProps = {
  view: View; // 当前视图，用于高亮激活入口
  onSwitch: (view: View) => void;
};

// 底部工具条（输入框上方，常驻不随切视图消失）：聊天 / 内在状态 / 欲望 / 活动 / 记忆 / 读书。
// 六个入口替换书卷区内容（切视图），当前入口高亮。未来加词条只需往 ENTRIES 追加一项。
const ENTRIES: readonly { label: string; view: View }[] = [
  { label: "内在状态", view: "inner" },
  { label: "欲望", view: "desire" },
  { label: "活动", view: "activity" },
  { label: "记忆", view: "memory" },
  { label: "读书", view: "reading" },
];

export default function RightDock({ view, onSwitch }: RightDockProps) {
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
    </div>
  );
}
