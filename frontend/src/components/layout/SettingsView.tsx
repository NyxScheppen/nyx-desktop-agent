import { useSettingsStore } from "../../stores/settingsStore";
import BackgroundPanel from "../panels/BackgroundPanel";
import Panel from "./Panel";

// 游戏设置页内面板（替换书卷区）：字体大小 + 背景外观（色调/背景图）。
// 复用 BackgroundPanel（纯前端 settingsStore）；字体大小原在 RightDock 折叠条，随「切视图」迁入。
const FONT_OPTIONS = [
  ["small", "小"],
  ["medium", "中"],
  ["large", "大"],
] as const;

export default function SettingsView() {
  const fontScale = useSettingsStore((s) => s.fontScale);
  const setFontScale = useSettingsStore((s) => s.setFontScale);

  return (
    <section className="side-panel">
      <header className="side-panel__header">
        <span className="side-panel__title">游戏设置</span>
      </header>
      <div className="side-panel__body">
        <Panel title="字体大小">
          <div className="font-scale">
            {FONT_OPTIONS.map(([key, label]) => (
              <button
                key={key}
                type="button"
                className={`font-scale__opt${
                  fontScale === key ? " font-scale__opt--active" : ""
                }`}
                aria-pressed={fontScale === key}
                onClick={() => setFontScale(key)}
              >
                {label}
              </button>
            ))}
          </div>
        </Panel>
        <BackgroundPanel />
      </div>
    </section>
  );
}
