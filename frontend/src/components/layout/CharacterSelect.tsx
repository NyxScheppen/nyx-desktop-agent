import { useCharacterStore } from "../../stores/characterStore";

export default function CharacterSelect() {
  const registry = useCharacterStore((s) => s.registry);
  const switching = useCharacterStore((s) => s.switching);
  const select = useCharacterStore((s) => s.select);
  if (!registry) return null;
  return <select aria-label="当前角色" value={registry.current_id} disabled={switching}
    onChange={(event) => void select(event.target.value)}>
    {registry.characters.filter((role) => role.status !== "ARCHIVED").map((role) =>
      <option key={role.id} value={role.id} disabled={role.status !== "RUNNING"}>
        {role.name}{role.status === "RUNNING" ? "" : `（${role.status}）`}
      </option>)}
  </select>;
}
