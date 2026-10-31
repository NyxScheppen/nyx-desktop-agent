import { useSettingsStore } from "../../stores/settingsStore";
import { useConnectionStore } from "../../stores/connectionStore";
import BackgroundPanel from "../panels/BackgroundPanel";
import CharacterManager from "./CharacterManager";
import Modal from "./Modal";
import Panel from "./Panel";
import { createPairingCode, getDevices, revokeDevice, type DeviceRecord } from "../../api/client";
import { useEffect, useState } from "react";

// 设置页面（桌宠保留弹层入口）：字体大小 + 背景外观（色调/背景图）+ 圆圈背景（可拖拽头像底色）+ 圆圈大小（三档）。
// 复用 BackgroundPanel（纯前端 settingsStore）；圆圈底色单独一组预设（名称与色调预设错开，避免测试按名选色歧义）。
const FONT_OPTIONS = [
  ["small", "小"],
  ["medium", "中"],
  ["large", "大"],
] as const;

const CIRCLE_SIZE_OPTIONS = [
  ["small", "小"],
  ["medium", "中"],
  ["large", "大"],
] as const;

const CIRCLE_COLOR_PRESETS: readonly { name: string; hex: string }[] = [
  { name: "白", hex: "#ffffff" },
  { name: "浅粉", hex: "#f7e8e0" },
  { name: "浅蓝", hex: "#dcebf5" },
  { name: "浅绿", hex: "#e0ead6" },
  { name: "浅橙", hex: "#f6e3d0" },
  { name: "淡紫", hex: "#e6ddf0" },
];

type SettingsViewProps = {
  onClose?: () => void;
};

export default function SettingsView({ onClose }: SettingsViewProps) {
  const token = useConnectionStore((s) => s.token);
  const [devices, setDevices] = useState<DeviceRecord[]>([]);
  const [pairCode, setPairCode] = useState("");
  const [deviceName, setDeviceName] = useState("");
  const [deviceError, setDeviceError] = useState("");
  const fontScale = useSettingsStore((s) => s.fontScale);
  const setFontScale = useSettingsStore((s) => s.setFontScale);
  const circleColor = useSettingsStore((s) => s.circleColor);
  const setCircleColor = useSettingsStore((s) => s.setCircleColor);
  const circleSize = useSettingsStore((s) => s.circleSize);
  const setCircleSize = useSettingsStore((s) => s.setCircleSize);
  const layoutStyle = useSettingsStore((s) => s.layoutStyle);
  const setLayoutStyle = useSettingsStore((s) => s.setLayoutStyle);

  useEffect(() => {
    if (!token) return;
    void getDevices().then(setDevices).catch(() => setDevices([]));
  }, [token]);

  async function issuePairingCode() {
    setDeviceError("");
    try {
      const result = await createPairingCode(deviceName.trim());
      setPairCode(result.code);
    } catch (error) {
      setDeviceError(error instanceof Error ? error.message : "配对码创建失败");
    }
  }

  async function removeDevice(id: string) {
    await revokeDevice(id);
    setDevices((current) => current.map((device) => device.id === id
      ? { ...device, revoked_at: device.revoked_at ?? Date.now() / 1000 }
      : device));
  }

  const content = (
      <div className="settings-layout">
      <section className="settings-appearance" aria-label="外观">
      <h2>外观</h2>
      {token && <Panel title="连接">
        <button onClick={() => useConnectionStore.getState().clearToken()}>清除访问凭证</button>
        <label>新设备名称<input value={deviceName} onChange={(event) => setDeviceName(event.target.value)} /></label>
        <button type="button" onClick={() => void issuePairingCode()}>生成手机配对码</button>
        {pairCode && <p role="status">配对码：{pairCode}</p>}
        {deviceError && <p role="alert">{deviceError}</p>}
        <ul aria-label="已配对设备">
          {devices.map((device) => <li key={device.id}>
            {device.name}{device.revoked_at ? "（已撤销）" : <button type="button" onClick={() => void removeDevice(device.id)}>撤销</button>}
          </li>)}
        </ul>
      </Panel>}
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
      <Panel title="窗口风格">
        <div className="style-switch" role="group" aria-label="窗口风格">
          <button type="button" aria-pressed={layoutStyle === "folio"} onClick={() => setLayoutStyle("folio")}>双开本</button>
          <button type="button" aria-pressed={layoutStyle === "chronicle"} onClick={() => setLayoutStyle("chronicle")}>编年史</button>
        </div>
      </Panel>
      <Panel title="圆圈背景">
        <div className="bg-panel">
          <div className="bg-panel__row">
            <span className="bg-panel__label">底色</span>
            <div className="bg-panel__swatches">
              {CIRCLE_COLOR_PRESETS.map((p) => (
                <button
                  key={p.hex}
                  type="button"
                  className={`bg-panel__swatch ${
                    circleColor === p.hex ? "bg-panel__swatch--active" : ""
                  }`}
                  style={{ backgroundColor: p.hex }}
                  title={p.name}
                  aria-label={p.name}
                  aria-pressed={circleColor === p.hex}
                  onClick={() => setCircleColor(p.hex)}
                />
              ))}
              <input
                type="color"
                className="bg-panel__color"
                value={circleColor}
                aria-label="自定义圆圈底色"
                onChange={(e) => setCircleColor(e.target.value)}
              />
            </div>
          </div>
        </div>
      </Panel>
      <Panel title="圆圈大小">
        <div className="font-scale">
          {CIRCLE_SIZE_OPTIONS.map(([key, label]) => (
            <button
              key={key}
              type="button"
              className={`font-scale__opt${
                circleSize === key ? " font-scale__opt--active" : ""
              }`}
              aria-pressed={circleSize === key}
              aria-label={`圆圈${label}`}
              onClick={() => setCircleSize(key)}
            >
              {label}
            </button>
          ))}
        </div>
      </Panel>
      <BackgroundPanel />
      </section>
      <CharacterManager />
      </div>
  );

  return onClose ? (
    <Modal title="设置" onClose={onClose}>{content}</Modal>
  ) : (
    <section className="settings-page" aria-label="设置">{content}</section>
  );
}
