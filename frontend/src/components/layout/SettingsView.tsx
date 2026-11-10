import { useSettingsStore } from "../../stores/settingsStore";
import { useConnectionStore } from "../../stores/connectionStore";
import CharacterCreator from "./CharacterCreator";
import CharacterManager from "./CharacterManager";
import CharacterCardView from "./CharacterCardView";
import { useCharacterStore } from "../../stores/characterStore";
import Modal from "./Modal";
import Panel from "./Panel";
import { createPairingCode, getDevices, revokeDevice, type DeviceRecord } from "../../api/client";
import { useEffect, useState } from "react";

// 完整端使用设置页，桌宠与未就绪状态保留弹层入口。
const FONT_OPTIONS = [
  ["small", "小"],
  ["medium", "中"],
  ["large", "大"],
] as const;

type SettingsViewProps = {
  onClose?: () => void;
  onCreateCharacter?: () => void;
};

export default function SettingsView({ onClose, onCreateCharacter }: SettingsViewProps) {
  const [creating, setCreating] = useState(false);
  const characterId = useCharacterStore((s) => s.registry?.current_id);
  const token = useConnectionStore((s) => s.token);
  const [devices, setDevices] = useState<DeviceRecord[]>([]);
  const [pairCode, setPairCode] = useState("");
  const [deviceName, setDeviceName] = useState("");
  const [deviceError, setDeviceError] = useState("");
  const fontScale = useSettingsStore((s) => s.fontScale);
  const setFontScale = useSettingsStore((s) => s.setFontScale);
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

  const content = creating ? <CharacterCreator onBack={() => setCreating(false)} /> : (
      <div className="settings-layout">
      <div className="settings-sidebar">
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
      </section>
      <CharacterManager onCreate={onCreateCharacter ?? (() => setCreating(true))} />
      </div>
      <Panel title="角色卡" className="settings-card">
        {characterId ? <CharacterCardView key={characterId} id={characterId} /> : <p className="panel-item__meta">选择角色后查看角色卡。</p>}
      </Panel>
      </div>
  );

  return onClose ? (
    <Modal title={creating ? "新建角色卡" : "设置"} onClose={onClose}>{content}</Modal>
  ) : (
    <section className="settings-page" aria-label="设置">{content}</section>
  );
}
