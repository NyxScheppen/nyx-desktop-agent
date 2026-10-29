import { useRef, useState, type FormEvent } from "react";
import { useCharacterStore } from "../../stores/characterStore";
import CharacterSelect from "./CharacterSelect";
import Panel from "./Panel";
import nyxCanon from "../../../../nyx/cards/nyx/canon.md?raw";

export default function CharacterManager() {
  const { registry, error, import: importCard, create, change, refresh } = useCharacterStore();
  const fileRef = useRef<HTMLInputElement>(null);
  const [creating, setCreating] = useState(false);
  const [form, setForm] = useState({ id: "", name: "", identity: "" });
  const [images, setImages] = useState<File[]>([]);
  const createRole = async (event: FormEvent) => {
    event.preventDefault();
    const dimensions = (keys: string[]) => Object.fromEntries(keys.map((key) => [key, 5]));
    await create({ ...form, personality: dimensions(["openness", "conscientiousness", "extraversion", "agreeableness", "neuroticism"]), values: dimensions(["attitude_to_human", "ai_identity_acceptance", "altruism", "optimism"]), aesthetic: dimensions(["ornate", "lyrical", "classical", "somber"]) }, images);
    setForm({ id: "", name: "", identity: "" }); setImages([]); setCreating(false);
  };
  return <Panel title={`角色管理（${registry?.characters.filter((role) => role.status !== "ARCHIVED").length ?? 0}/4）`} className="character-manager">
    <div className="character-manager__toolbar">
      <CharacterSelect />
      <input ref={fileRef} type="file" accept=".nyxcard,.zip" hidden onChange={(event) => {
        const file = event.target.files?.[0];
        if (file) void importCard(file);
        event.target.value = "";
      }} />
      <button type="button" className="bg-panel__btn" onClick={() => fileRef.current?.click()}>导入角色卡</button>
      <button type="button" className="bg-panel__btn" onClick={() => setCreating((value) => !value)}>{creating ? "取消新建" : "新建角色"}</button>
      <button type="button" className="bg-panel__btn" onClick={() => void refresh()}>刷新角色状态</button>
    </div>
    {creating && <form className="character-manager__form" onSubmit={(event) => void createRole(event)}>
      <input required pattern="[a-z][a-z0-9_-]{0,63}" placeholder="角色 ID" value={form.id} onChange={(event) => setForm({ ...form, id: event.target.value })} />
      <input required maxLength={100} placeholder="角色名称" value={form.name} onChange={(event) => setForm({ ...form, name: event.target.value })} />
      <textarea required maxLength={6000} placeholder="身份设定" value={form.identity} onChange={(event) => setForm({ ...form, identity: event.target.value })} />
      <input type="file" accept="image/*" multiple onChange={(event) => setImages(Array.from(event.target.files ?? []))} />
      <button type="submit" className="bg-panel__btn">创建角色</button>
    </form>}
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
    {registry?.current_id === "nyx" && <div className="character-manager__canon">
      <h3>尼克斯 · 初始人设</h3>
      <pre>{nyxCanon}</pre>
    </div>}
  </Panel>;
}
