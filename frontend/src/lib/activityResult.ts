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

/** 活动状态一句话（读当前活动）：「在读《X》/在探索/在创作/在观察你/在静默反思/在休息/空闲」。 */
export function activityStatusText(a: Activity | null): string {
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

/** 已完成活动的产出文案（读书 {book,note} / 创作 {title,content} / 探索 {summary,core_discovery}）；无产出返回 null。 */
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
    if (typeof r.summary === "string" && r.summary.length > 0) parts.push(r.summary);
    if (typeof r.core_discovery === "string" && r.core_discovery.length > 0)
      parts.push(r.core_discovery);
    return parts.length > 0 ? parts.join(" — ") : null;
  }
  return null;
}

/** 产出面板正文：完整产出文本（多行），与 formatResult 的单行摘要互补。
 *  读书→笔记、创作→内容、探索→核心发现+知识逐条；无产出返回 null。 */
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
    if (typeof r.core_discovery === "string" && r.core_discovery.length > 0)
      lines.push(`核心发现：${r.core_discovery}`);
    if (Array.isArray(r.knowledge)) {
      for (const k of r.knowledge) {
        if (typeof k !== "object" || k === null) continue;
        const kk = k as Record<string, unknown>;
        const topic = typeof kk.topic === "string" ? kk.topic : "";
        const content = typeof kk.content === "string" ? kk.content : "";
        if (content.length === 0) continue;
        lines.push(topic.length > 0 ? `【${topic}】${content}` : content);
      }
    }
    if (lines.length === 0 && typeof r.summary === "string" && r.summary.length > 0)
      lines.push(r.summary);
    return lines.length > 0 ? lines.join("\n") : null;
  }
  return null;
}

/** 单次工具调用的文案（{name, args} → 「联网搜索「X」」）；无法识别返回 null。 */
function formatToolCall(name: string, args: Record<string, unknown>): string | null {
  switch (name) {
    case "web_search":
      return `联网搜索「${typeof args.query === "string" ? args.query : ""}」`;
    case "local_search":
      return `本地搜索「${typeof args.query === "string" ? args.query : ""}」`;
    case "web_fetch":
      return `抓取网页 ${typeof args.url === "string" ? args.url : ""}`;
    case "file_io":
      return `写文件 ${typeof args.path === "string" ? args.path : ""}`;
    default:
      return name.length > 0 ? name : null;
  }
}

/** 产出面板工具轨迹：result.tools（数组）→ 「联网搜索「X」 → 抓取网页 Y」；无 tools 返回 null。 */
export function formatTools(a: Activity): string | null {
  if (a.status !== "completed") return null;
  const result = a.progress.result;
  if (typeof result !== "object" || result === null) return null;
  const r = result as Record<string, unknown>;
  if (!Array.isArray(r.tools)) return null;
  const parts: string[] = [];
  for (const t of r.tools) {
    if (typeof t !== "object" || t === null) continue;
    const tc = t as Record<string, unknown>;
    const name = typeof tc.name === "string" ? tc.name : "";
    const args =
      typeof tc.args === "object" && tc.args !== null
        ? (tc.args as Record<string, unknown>)
        : {};
    const label = formatToolCall(name, args);
    if (label === null) continue;
    parts.push(tc.ok === false ? `${label}（失败）` : label);
  }
  return parts.length > 0 ? parts.join(" → ") : null;
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
