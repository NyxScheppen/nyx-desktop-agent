import { formatPolicyText } from "./policy";
import runtimePolicy from "../../../nyx/settings/runtime.json";
import frontendPolicy from "../../../nyx/settings/frontend.json";
export type TimePhase = "day" | "night";

type TimedMessage = {
  kind: string;
  correlation_id: string;
  timestamp: number;
};

const WEEKDAYS = frontendPolicy.frontend.time.WEEKDAYS;
const RESPONSE_KINDS = new Set(["think", "speak", "ask"]);

export function isValidTimestamp(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value) &&
    Number.isFinite(new Date(value * 1000).getTime());
}

function pad(value: number): string {
  return String(value).padStart(2, "0");
}

export const USER_TIME_ZONE = runtimePolicy.timezone.name;

function calendarAt(value: Date): Date {
  return new Date(value.getTime() + runtimePolicy.timezone.offset_hours * 60 * 60 * 1000);
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
    : frontendPolicy.copy.time.text;
}

export function formatDate(timestamp: number): string {
  return isValidTimestamp(timestamp)
    ? new Date(timestamp * 1000).toLocaleDateString("zh-CN", { timeZone: USER_TIME_ZONE })
    : frontendPolicy.copy.time.text;
}

export function formatClock(timestamp: number): string {
  return isValidTimestamp(timestamp)
    ? new Date(timestamp * 1000).toLocaleTimeString("zh-CN", { timeZone: USER_TIME_ZONE, hour12: false })
    : frontendPolicy.copy.time.text;
}

export function timePhaseAt(value: Date): TimePhase {
  const hour = calendarAt(value).getUTCHours();
  return hour >= frontendPolicy.frontend.time.night_start_hour ||
    hour < frontendPolicy.frontend.time.day_start_hour ? "night" : "day";
}

export function formatCurrentTime(value: Date): string {
  const local = calendarAt(value);
  return (
    formatPolicyText(frontendPolicy.templates.time.text, { p0: local.getUTCMonth() + 1, p1: local.getUTCDate() }) +
    formatPolicyText(frontendPolicy.templates.time.text_2, { p0: WEEKDAYS[local.getUTCDay()], p1: pad(local.getUTCHours()), p2: pad(local.getUTCMinutes()) })
  );
}

export function formatMessageTime(timestamp: number, now: Date): string {
  if (!isValidTimestamp(timestamp)) return frontendPolicy.copy.time.text;
  const raw = new Date(timestamp * 1000);
  const value = calendarAt(raw);
  const clock = `${pad(value.getUTCHours())}:${pad(value.getUTCMinutes())}`;
  if (sameLocalDate(raw, now)) return formatPolicyText(frontendPolicy.templates.time.text_3, { p0: clock });
  const dayGap = Math.round((startOfLocalDay(now) - startOfLocalDay(raw)) / 86_400_000);
  if (dayGap === 1) return formatPolicyText(frontendPolicy.templates.time.text_4, { p0: clock });
  return formatPolicyText(frontendPolicy.templates.time.text_5, { p0: value.getUTCMonth() + 1, p1: value.getUTCDate(), p2: WEEKDAYS[value.getUTCDay()], p3: clock });
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
  return current.timestamp - previous.timestamp >= frontendPolicy.frontend.time.divider_gap_seconds;
}
