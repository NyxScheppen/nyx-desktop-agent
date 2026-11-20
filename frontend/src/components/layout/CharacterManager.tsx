import { formatPolicyText } from "../../lib/policy";
import characterPolicy from "../../../../nyx/settings/character.json";
import { useRef } from "react";
import { useCharacterStore } from "../../stores/characterStore";
import CharacterSelect from "./CharacterSelect";
import Panel from "./Panel";

export default function CharacterManager({ onCreate }: { onCreate: () => void }) {
  const { registry, error, import: importCard, change, refresh } = useCharacterStore();
  const fileRef = useRef<HTMLInputElement>(null);
  return <Panel title={formatPolicyText(characterPolicy.templates.CharacterManager.text, { p0: registry?.characters.filter((role) => role.status !== "ARCHIVED").length ?? 0 })} className="character-manager">
    <div className="character-manager__toolbar">
      <CharacterSelect />
      <input ref={fileRef} type="file" accept=".nyxcard,.zip" hidden onChange={(event) => {
        const file = event.target.files?.[0];
        if (file) void importCard(file);
        event.target.value = "";
      }} />
      <button type="button" className="bg-panel__btn" onClick={() => fileRef.current?.click()}>{characterPolicy.copy.CharacterManager.button}</button>
      <button type="button" className="bg-panel__btn" onClick={onCreate}>{characterPolicy.copy.CharacterManager.button_2}</button>
      <button type="button" className="bg-panel__btn" onClick={() => void refresh()}>{characterPolicy.copy.CharacterManager.button_3}</button>
    </div>
    {error && <p role="alert">{error}</p>}
    {registry?.characters.map((role) => <div className="character-manager__row" key={role.id}>
      <strong>{role.name}</strong> · {role.status}
      {role.error && <p role="alert">{role.error}</p>}
      {role.sync_error && <p role="alert">{role.sync_error}</p>}
      <div className="character-manager__actions">
        {role.status === "ARCHIVED" ? <button type="button" className="bg-panel__btn" onClick={() => void change(role.id, "restore")}>{characterPolicy.copy.CharacterManager.button_4}</button> : <>
          {role.status === "FAILED" && <button type="button" className="bg-panel__btn" onClick={() => void change(role.id, "retry-start")}>{characterPolicy.copy.CharacterManager.button_5}</button>}
          <button type="button" className="bg-panel__btn" disabled={role.status === "STOPPING" || role.status === "STARTING"}
            onClick={() => void change(role.id, "unload")}>{characterPolicy.copy.CharacterManager.button_6}</button>
        </>}
      </div>
    </div>)}
  </Panel>;
}
