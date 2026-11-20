import frontendPolicy from "../../../../nyx/settings/frontend.json";
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
const FONT_OPTIONS = frontendPolicy.frontend.SettingsView.FONT_OPTIONS as [
  Parameters<ReturnType<typeof useSettingsStore.getState>["setFontScale"]>[0],
  string,
][];

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
      setDeviceError(error instanceof Error ? error.message : frontendPolicy.copy.SettingsView.text);
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
      <section className="settings-appearance" aria-label={frontendPolicy.copy.SettingsView.aria_label}>
      <h2>{frontendPolicy.copy.SettingsView.aria_label}</h2>
      {token && <Panel title={frontendPolicy.copy.SettingsView.title}>
        <button onClick={() => useConnectionStore.getState().clearToken()}>{frontendPolicy.copy.SettingsView.button}</button>
        <label>{frontendPolicy.copy.SettingsView.label}<input value={deviceName} onChange={(event) => setDeviceName(event.target.value)} /></label>
        <button type="button" onClick={() => void issuePairingCode()}>{frontendPolicy.copy.SettingsView.button_2}</button>
        {pairCode && <p role="status">{frontendPolicy.copy.SettingsView.p}{pairCode}</p>}
        {deviceError && <p role="alert">{deviceError}</p>}
        <ul aria-label={frontendPolicy.copy.SettingsView.aria_label_2}>
          {devices.map((device) => <li key={device.id}>
            {device.name}{device.revoked_at ? frontendPolicy.copy.SettingsView.text_2 : <button type="button" onClick={() => void removeDevice(device.id)}>{frontendPolicy.copy.SettingsView.button_3}</button>}
          </li>)}
        </ul>
      </Panel>}
      <Panel title={frontendPolicy.copy.SettingsView.title_2}>
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
      <Panel title={frontendPolicy.copy.SettingsView.title_3}>
        <div className="style-switch" role="group" aria-label={frontendPolicy.copy.SettingsView.title_3}>
          <button type="button" aria-pressed={layoutStyle === "folio"} onClick={() => setLayoutStyle("folio")}>{frontendPolicy.copy.SettingsView.button_4}</button>
          <button type="button" aria-pressed={layoutStyle === "chronicle"} onClick={() => setLayoutStyle("chronicle")}>{frontendPolicy.copy.SettingsView.button_5}</button>
        </div>
      </Panel>
      </section>
      <CharacterManager onCreate={onCreateCharacter ?? (() => setCreating(true))} />
      </div>
      <Panel title={frontendPolicy.copy.SettingsView.title_4} className="settings-card">
        {characterId ? <CharacterCardView key={characterId} id={characterId} /> : <p className="panel-item__meta">{frontendPolicy.copy.SettingsView.p_2}</p>}
      </Panel>
      </div>
  );

  return onClose ? (
    <Modal title={creating ? frontendPolicy.copy.SettingsView.text_3 : frontendPolicy.copy.SettingsView.text_4} onClose={onClose}>{content}</Modal>
  ) : (
    <section className="settings-page" aria-label={frontendPolicy.copy.SettingsView.text_4}>{content}</section>
  );
}
