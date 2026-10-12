import { useCharacterStore } from "../../stores/characterStore";
import CharacterSelect from "./CharacterSelect";

export default function CharacterManager() {
  const { registry, error, import: importCard, change, refresh } = useCharacterStore();
  return <section aria-label="角色管理">
    <h3>角色管理（{registry?.characters.filter((role) => role.status !== "ARCHIVED").length ?? 0}/4）</h3>
    <CharacterSelect />
    <label>导入角色卡 <input type="file" accept=".nyxcard,.zip" onChange={(event) => {
      const file = event.target.files?.[0];
      if (file) void importCard(file);
      event.target.value = "";
    }} /></label>
    <button type="button" onClick={() => void refresh()}>刷新角色状态</button>
    {error && <p role="alert">{error}</p>}
    {registry?.characters.map((role) => <div key={role.id}>
      <strong>{role.name}</strong> · {role.status}
      {role.error && <p role="alert">{role.error}</p>}
      {role.sync_error && <p role="alert">{role.sync_error}</p>}
      {role.status === "ARCHIVED" ? <button onClick={() => void change(role.id, "restore")}>恢复</button> : <>
        {role.status === "FAILED" && <button onClick={() => void change(role.id, "retry-start")}>重试启动</button>}
        <button disabled={role.status === "STOPPING" || role.status === "STARTING"}
          onClick={() => void change(role.id, "unload")}>卸载并保留存档</button>
      </>}
    </div>)}
  </section>;
}
