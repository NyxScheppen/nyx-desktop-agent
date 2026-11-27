import { useEffect, useRef, useState, type FormEvent } from "react";
import frontendPolicy from "../../../../nyx/settings/frontend.json";
import initiativePolicy from "../../../../nyx/settings/initiative.json";
import { getInitiativeSettings, saveInitiativeSettings } from "../../api/client";
import Panel from "./Panel";

export default function InitiativeSettings() {
  const request = useRef<AbortController | null>(null);
  const [saved, setSaved] = useState<number | null>(null);
  const [draft, setDraft] = useState("");
  const [saving, setSaving] = useState(false);
  const [feedback, setFeedback] = useState<"saved" | "error" | null>(null);
  const copy = frontendPolicy.initiative;

  useEffect(() => {
    const controller = new AbortController();
    request.current = controller;
    void getInitiativeSettings(controller.signal).then(({ cooldown_minutes }) => {
      if (controller.signal.aborted) return;
      setSaved(cooldown_minutes);
      setDraft(String(cooldown_minutes));
    }).catch(() => {
      if (!controller.signal.aborted) setFeedback("error");
    });
    return () => request.current?.abort();
  }, []);

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const minutes = Number(draft);
    if (saving || saved === null || minutes === saved) return;
    const controller = new AbortController();
    request.current = controller;
    setSaving(true);
    setFeedback(null);
    try {
      const { cooldown_minutes } = await saveInitiativeSettings(minutes, controller.signal);
      if (controller.signal.aborted) return;
      setSaved(cooldown_minutes);
      setDraft(String(cooldown_minutes));
      setFeedback("saved");
    } catch {
      if (!controller.signal.aborted) setFeedback("error");
    } finally {
      if (!controller.signal.aborted) setSaving(false);
    }
  }

  return (
    <Panel title={copy.title}>
      <form onSubmit={(event) => void save(event)}>
        <label>
          {copy.cooldown}
          <input type="number" required value={draft}
            min={initiativePolicy.cooldown_min_minutes}
            max={initiativePolicy.cooldown_max_minutes}
            disabled={saved === null || saving}
            onChange={(event) => { setDraft(event.target.value); setFeedback(null); }}
          />
        </label>
        <button type="submit" disabled={saved === null || saving || !draft || Number(draft) === saved}>
          {copy.save}
        </button>
        {feedback && <p role={feedback === "error" ? "alert" : "status"}>{copy[feedback]}</p>}
      </form>
    </Panel>
  );
}
