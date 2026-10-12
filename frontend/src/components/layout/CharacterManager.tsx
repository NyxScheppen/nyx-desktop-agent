import { useRef } from "react";
import { useCharacterStore } from "../../stores/characterStore";
import CharacterSelect from "./CharacterSelect";
import Panel from "./Panel";

export default function CharacterManager() {
  const { registry, error, import: importCard, change, refresh } = useCharacterStore();
  const fileRef = useRef<HTMLInputElement>(null);
  return <Panel title={`角色管理（${registry?.characters.filter((role) => role.status !== "ARCHIVED").length ?? 0}/4）`} className="character-manager">
    <div className="character-manager__toolbar">
      <CharacterSelect />
      <input ref={fileRef} type="file" accept=".nyxcard,.zip" hidden onChange={(event) => {
        const file = event.target.files?.[0];
        if (file) void importCard(file);
        event.target.value = "";
      }} />
      <button type="button" className="bg-panel__btn" onClick={() => fileRef.current?.click()}>导入角色卡</button>
      <button type="button" className="bg-panel__btn" onClick={() => void refresh()}>刷新角色状态</button>
    </div>
    {error && <p role="alert">{error}</p>}
    {registry?.characters.map((role) => <div className="character-manager__row" key={role.id}>
      <strong>{role.name}</strong> · {role.status}
      {role.error && <p role="alert">{role.error}</p>}
      {role.sync_error && <p role="alert">{role.sync_error}</p>}
      <div className="character-manager__actions">
        {role.status === "ARCHIVED" ? <button type="button" className="bg-panel__btn" onClick={() => void change(role.id, "restore")}>恢复</button> : <>
          {role.status === "FAILED" && <button type="button" className="bg-panel__btn" onClick={() => void change(role.id, "retry-start")}>重试启动</button>}
          <button type="button" className="bg-panel__btn" disabled={role.status === "STOPPING" || role.status === "STARTING"}
            onClick={() => void change(role.id, "unload")}>卸载并保留存档</button>
        </>}
      </div>
    </div>)}
  </Panel>;
}
