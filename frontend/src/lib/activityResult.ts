import type { Activity, ActivityType } from "../types/api";

// 活动产出文案纯函数（14-activity §result 形状）。
// 单一来源：ActivityPanel（日程产出）+ StatusBar（主题）+ AnnounceLayer（完成后冒一句）共用。
// 键名 = 后端 progress 键（snake_case 零映射）；字段非预期类型则忽略不崩。

/** 活动主题：progress 里第一个非空字符串（阅读=filename、探索/创作=description、读书=source）。 */
export function activitySubject(a: Activity): string | null {
  const p = a.progress;
  for (const key of ["description", "filename", "source"]) {
    const v = p[key];
    if (typeof v === "string" && v.length > 0) return v;
  }
  return null;
}

/** 已完成活动的产出文案（读书 {book,note} / 创作 {title,content} / 探索 {findings,notes}）；无产出返回 null。 */
export function formatResult(a: Activity): string | null {
  if (a.status !== "completed") return null;
  const result = a.progress.result;
  if (typeof result !== "object" || result === null) return null;
  const r = result as Record<string, unknown>;
  if (a.type === "reading") {
    const parts: string[] = [];
    if (typeof r.book === "string") parts.push(r.book);
    if (typeof r.note === "string") parts.push(r.note);
    return parts.length > 0 ? parts.join(" — ") : null;
  }
  if (a.type === "creation") {
    const parts: string[] = [];
    if (typeof r.title === "string") parts.push(r.title);
    if (typeof r.content === "string") parts.push(r.content);
    return parts.length > 0 ? parts.join(" — ") : null;
  }
  if (a.type === "free_exploration") {
    const parts: string[] = [];
    if (Array.isArray(r.findings)) parts.push(...r.findings.map(String));
    if (Array.isArray(r.notes)) parts.push(...r.notes.map(String));
    return parts.length > 0 ? parts.join(" / ") : null;
  }
  return null;
}

/** 产出面板正文：完整产出文本（多行），与 formatResult 的单行摘要互补。
 *  读书→笔记、创作→内容、探索→发现+备注逐行；无产出返回 null。 */
export function formatOutputBody(a: Activity): string | null {
  if (a.status !== "completed") return null;
  const result = a.progress.result;
  if (typeof result !== "object" || result === null) return null;
  const r = result as Record<string, unknown>;
  if (a.type === "reading") {
    return typeof r.note === "string" && r.note.length > 0 ? r.note : null;
  }
  if (a.type === "creation") {
    return typeof r.content === "string" && r.content.length > 0 ? r.content : null;
  }
  if (a.type === "free_exploration") {
    const lines: string[] = [];
    if (Array.isArray(r.findings)) lines.push(...r.findings.map(String));
    if (Array.isArray(r.notes)) lines.push(...r.notes.map(String));
    return lines.length > 0 ? lines.join("\n") : null;
  }
  return null;
}

// 完成后「主动冒一句」的前缀：只有会产出 result 的三类活动。
const ANNOUNCE_PREFIX: Partial<Record<ActivityType, string>> = {
  reading: "读完啦：",
  creation: "创作完成：",
  free_exploration: "探索收获：",
};

/** 活动完成后主动冒一句的文案（前缀 + formatResult）；无产出返回 null。 */
export function activityAnnouncement(a: Activity): string | null {
  if (a.status !== "completed") return null;
  const result = formatResult(a);
  if (result === null) return null;
  const prefix = ANNOUNCE_PREFIX[a.type];
  return prefix !== undefined ? `${prefix}${result}` : null;
}
