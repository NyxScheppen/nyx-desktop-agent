import { useEffect, useState } from "react";
import policy from "../../../../nyx/settings/frontend.json";
import { getSchedules, changeSchedule, type ScheduledItem } from "../../api/client";
import { captureScope, isScopeCurrent } from "../../api/characterScope";
import Panel from "./Panel";

const copy = policy.copy.MobileSchedules;

export default function MobileSchedules() {
  const [items, setItems] = useState<ScheduledItem[] | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [editing, setEditing] = useState<string | null>(null);
  const [localTime, setLocalTime] = useState("");

  useEffect(() => {
    const controller = new AbortController();
    void getSchedules(controller.signal).then((result) => {
      if (!controller.signal.aborted) setItems(result);
    }).catch(() => {
      if (!controller.signal.aborted) setError(copy.unavailable);
    });
    return () => controller.abort();
  }, []);

  async function update(item: ScheduledItem, operation: Parameters<typeof changeSchedule>[2]) {
    if (busy) return;
    const scope = captureScope();
    setBusy(item.id);
    setError("");
    try {
      const changed = await changeSchedule(item.id, item.version, operation, operation === "reschedule" ? localTime : undefined);
      if (!isScopeCurrent(scope)) return;
      setItems((current) => current?.map((entry) => entry.id === item.id ? changed : entry) ?? null);
      setEditing(null);
    } catch {
      if (isScopeCurrent(scope)) {
        setError(copy.conflict);
        void getSchedules().then((latest) => {
          if (isScopeCurrent(scope)) setItems(latest);
        }).catch(() => {});
      }
    } finally {
      if (isScopeCurrent(scope)) setBusy(null);
    }
  }

  const active = items?.filter((item) => item.origin === "user" && item.action === "reading" &&
    (item.status === "active" || item.status === "paused"));
  return <Panel title={copy.title}>
    {error && <p role="alert">{error}</p>}
    {active?.length === 0 && <p>{copy.empty}</p>}
    {active?.map((item) => <section key={item.id} className="nyx-mobile__schedule">
      <strong>{item.note || copy.title}</strong>
      <p>{copy[item.status === "active" ? "active" : "paused"]} · {copy.recurrence[item.recurrence] ?? item.recurrence}</p>
      {item.next_local_time && <time dateTime={item.next_local_time}>{item.next_local_time.replace("T", " ")}</time>}
      <div>
        <button type="button" disabled={busy !== null} onClick={() => void update(item, item.status === "active" ? "pause" : "resume")}>
          {copy[item.status === "active" ? "pause" : "resume"]}
        </button>
        <button type="button" disabled={busy !== null} onClick={() => void update(item, "skip")}>{copy.skip}</button>
        <button type="button" disabled={busy !== null} onClick={() => {
          setEditing(item.id);
          setLocalTime(item.next_local_time?.slice(0, 16) ?? "");
        }}>{copy.reschedule}</button>
        <button type="button" disabled={busy !== null} onClick={() => {
          if (window.confirm(copy.confirm_cancel)) void update(item, "cancel");
        }}>{copy.cancel}</button>
      </div>
      {editing === item.id && <form onSubmit={(event) => {
        event.preventDefault();
        void update(item, "reschedule");
      }}>
        <label>{copy.time}<input type="datetime-local" value={localTime} required onChange={(event) => setLocalTime(event.target.value)} /></label>
        <button type="submit" disabled={busy !== null}>{copy.save}</button>
      </form>}
    </section>)}
  </Panel>;
}
