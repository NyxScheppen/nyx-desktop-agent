// 书卷区当前视图：null = 对话主舞台；number = InnerWorld 分类（0 内在 / 1 空间 / 2 记录）；
// "settings" = 游戏设置页；"explore" = 出门探索地图。
export type View = number | "settings" | "explore" | null;

type RightDockProps = {
  view: View; // 当前视图，用于高亮激活入口
  onSwitch: (view: View) => void;
};

// 右侧底部工具条（输入框上方，常驻不随切视图消失）：聊天 / 内在 / 空间 / 记录 / 出门 / 游戏设置。
// 六个入口替换书卷区内容（切视图），当前入口高亮；字体/背景设置迁至 SettingsView。
// 动作类词条（出门，将来的一起读书）与观测面板同一条，未来加词条只需往 ENTRIES 追加一项。
const ENTRIES: readonly { label: string; view: View }[] = [
  { label: "聊天", view: null },
  { label: "内在", view: 0 },
  { label: "空间", view: 1 },
  { label: "记录", view: 2 },
  { label: "出门", view: "explore" },
  { label: "游戏设置", view: "settings" },
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
