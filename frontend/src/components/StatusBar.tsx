import { activityStatusText } from "../lib/activityResult";
import { useActivityStore } from "../stores/activityStore";

export default function StatusBar() {
  const current = useActivityStore((s) => s.data?.current ?? null);
  return <div className="status-bar">{activityStatusText(current)}</div>;
}
