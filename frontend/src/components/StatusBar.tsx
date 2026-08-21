import { activitySubject } from "../lib/activityResult";
import { useActivityStore } from "../stores/activityStore";
import type { Activity } from "../types/api";

// 底部常驻状态条（frontend-design §6「当前活动文字」）：读 activityStore 当前活动，
// 显示「在读什么 / 在探索什么 / 在创作什么」，无活动显示「空闲」。
// 精力条不在本次范围（§6 后半句 defer），只做活动文字。
function statusText(a: Activity | null): string {
  if (a === null) return "空闲";
  const subject = activitySubject(a);
  switch (a.type) {
    case "reading":
      return subject !== null ? `在读《${subject}》` : "在读书";
    case "free_exploration":
      return subject !== null ? `在探索「${subject}」` : "在探索";
    case "creation":
      return subject !== null ? `在创作：${subject}` : "在创作";
    case "observe_user":
      return "在观察你";
    case "idle_reflection":
      return "在静默反思";
    case "rest":
      return "在休息";
  }
}

export default function StatusBar() {
  const current = useActivityStore((s) => s.data?.current ?? null);
  return <div className="status-bar">{statusText(current)}</div>;
}
