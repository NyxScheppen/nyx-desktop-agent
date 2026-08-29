import { useSettingsStore } from "../../stores/settingsStore";
import BackgroundPanel from "../panels/BackgroundPanel";
import Modal from "./Modal";
import Panel from "./Panel";

// 设置弹层：字体大小 + 背景外观（色调/背景图）。复用 BackgroundPanel（纯前端 settingsStore）。
const FONT_OPTIONS = [
  ["small", "小"],
  ["medium", "中"],
  ["large", "大"],
] as const;

type SettingsViewProps = {
  onClose: () => void;
};

export default function SettingsView({ onClose }: SettingsViewProps) {
  const fontScale = useSettingsStore((s) => s.fontScale);
  const setFontScale = useSettingsStore((s) => s.setFontScale);

  return (
    <Modal title="设置" onClose={onClose}>
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
    </Modal>
  );
}
