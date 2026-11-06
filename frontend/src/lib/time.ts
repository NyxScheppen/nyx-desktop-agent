export type TimePhase = "day" | "night";

type TimedMessage = {
  kind: string;
  correlation_id: string;
  timestamp: number;
};

const WEEKDAYS = ["日", "一", "二", "三", "四", "五", "六"] as const;
const RESPONSE_KINDS = new Set(["think", "speak", "ask"]);

export function isValidTimestamp(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value) &&
    Number.isFinite(new Date(value * 1000).getTime());
}

function pad(value: number): string {
  return String(value).padStart(2, "0");
}

export const USER_TIME_ZONE = "Asia/Shanghai";

function calendarAt(value: Date): Date {
  return new Date(value.getTime() + 8 * 60 * 60 * 1000);
}

function sameLocalDate(a: Date, b: Date): boolean {
  return calendarAt(a).toISOString().slice(0, 10) === calendarAt(b).toISOString().slice(0, 10);
}

function startOfLocalDay(value: Date): number {
  const local = calendarAt(value);
  return Date.UTC(local.getUTCFullYear(), local.getUTCMonth(), local.getUTCDate());
}

export function formatDateTime(timestamp: number): string {
  return isValidTimestamp(timestamp)
    ? new Date(timestamp * 1000).toLocaleString("zh-CN", { timeZone: USER_TIME_ZONE, hour12: false })
    : "时间未知";
}

export function formatDate(timestamp: number): string {
  return isValidTimestamp(timestamp)
    ? new Date(timestamp * 1000).toLocaleDateString("zh-CN", { timeZone: USER_TIME_ZONE })
    : "时间未知";
}

export function formatClock(timestamp: number): string {
  return isValidTimestamp(timestamp)
    ? new Date(timestamp * 1000).toLocaleTimeString("zh-CN", { timeZone: USER_TIME_ZONE, hour12: false })
    : "时间未知";
}

export function timePhaseAt(value: Date): TimePhase {
  const hour = calendarAt(value).getUTCHours();
  return hour >= 22 || hour < 6 ? "night" : "day";
}

export function formatCurrentTime(value: Date): string {
  const local = calendarAt(value);
  return (
    `${local.getUTCMonth() + 1}月${local.getUTCDate()}日 ` +
    `星期${WEEKDAYS[local.getUTCDay()]} ${pad(local.getUTCHours())}:${pad(local.getUTCMinutes())}`
  );
}

export function formatMessageTime(timestamp: number, now: Date): string {
  if (!isValidTimestamp(timestamp)) return "时间未知";
  const raw = new Date(timestamp * 1000);
  const value = calendarAt(raw);
  const clock = `${pad(value.getUTCHours())}:${pad(value.getUTCMinutes())}`;
  if (sameLocalDate(raw, now)) return `今天 ${clock}`;
  const dayGap = Math.round((startOfLocalDay(now) - startOfLocalDay(raw)) / 86_400_000);
  if (dayGap === 1) return `昨天 ${clock}`;
  return `${value.getUTCMonth() + 1}月${value.getUTCDate()}日 周${WEEKDAYS[value.getUTCDay()]} ${clock}`;
}

export function shouldShowTimeDivider(
  current: TimedMessage,
  previous: TimedMessage | null,
): boolean {
  if (previous === null) return true;
  if (
    current.correlation_id === previous.correlation_id &&
    RESPONSE_KINDS.has(current.kind) &&
    RESPONSE_KINDS.has(previous.kind)
  ) {
    return false;
  }
  const currentDate = new Date(current.timestamp * 1000);
  const previousDate = new Date(previous.timestamp * 1000);
  if (!sameLocalDate(currentDate, previousDate)) return true;
  return current.timestamp - previous.timestamp >= 30 * 60;
}
