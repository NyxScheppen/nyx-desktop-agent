import { useRef, type ChangeEvent } from "react";
import { useSettingsStore } from "../../stores/settingsStore";
import Panel from "../layout/Panel";

// 背景外观面板（视觉改造 §5）：预设色调色块 + 自定义取色 + 上传背景图 + 恢复默认。
// 纯前端：读写 settingsStore，图片转 data URL 存内存（MVP 不持久化）。
const TINT_PRESETS: readonly { name: string; hex: string }[] = [
  { name: "樱粉", hex: "#f7e8e0" },
  { name: "晨蓝", hex: "#dcebf5" },
  { name: "森绿", hex: "#e0ead6" },
  { name: "暖橙", hex: "#f6e3d0" },
  { name: "暮紫", hex: "#e6ddf0" },
  { name: "夜蓝", hex: "#1f2740" },
];

export default function BackgroundPanel() {
  const tint = useSettingsStore((s) => s.tint);
  const image = useSettingsStore((s) => s.image);
  const setTint = useSettingsStore((s) => s.setTint);
  const setImage = useSettingsStore((s) => s.setImage);
  const reset = useSettingsStore((s) => s.reset);
  const fileRef = useRef<HTMLInputElement | null>(null);

  const onFile = (e: ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (file === undefined) return;
    const reader = new FileReader();
    reader.onload = () => setImage(typeof reader.result === "string" ? reader.result : null);
    reader.readAsDataURL(file);
    e.target.value = ""; // 允许重复选同一文件
  };

  return (
    <Panel title="背景">
      <div className="bg-panel">
        <div className="bg-panel__row">
          <span className="bg-panel__label">色调</span>
          <div className="bg-panel__swatches">
            {TINT_PRESETS.map((p) => (
              <button
                key={p.hex}
                type="button"
                className={`bg-panel__swatch ${tint === p.hex ? "bg-panel__swatch--active" : ""}`}
                style={{ backgroundColor: p.hex }}
                title={p.name}
                aria-label={p.name}
                aria-pressed={tint === p.hex}
                onClick={() => setTint(p.hex)}
              />
            ))}
            <input
              type="color"
              className="bg-panel__color"
              value={tint ?? "#f7e8e0"}
              aria-label="自定义色调"
              onChange={(e) => setTint(e.target.value)}
            />
          </div>
        </div>

        <div className="bg-panel__row">
          <span className="bg-panel__label">背景图</span>
          <div className="bg-panel__actions">
            <input
              ref={fileRef}
              type="file"
              accept="image/*"
              className="bg-panel__file"
              hidden
              onChange={onFile}
            />
            <button
              type="button"
              className="bg-panel__btn"
              onClick={() => fileRef.current?.click()}
            >
              {image === null ? "上传图片" : "更换图片"}
            </button>
            {image !== null && (
              <button type="button" className="bg-panel__btn" onClick={() => setImage(null)}>
                移除图片
              </button>
            )}
            {(tint !== null || image !== null) && (
              <button type="button" className="bg-panel__btn" onClick={reset}>
                恢复默认
              </button>
            )}
          </div>
        </div>
      </div>
    </Panel>
  );
}
