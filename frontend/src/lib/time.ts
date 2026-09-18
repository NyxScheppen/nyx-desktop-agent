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

function sameLocalDate(a: Date, b: Date): boolean {
  return (
    a.getFullYear() === b.getFullYear() &&
    a.getMonth() === b.getMonth() &&
    a.getDate() === b.getDate()
  );
}

function startOfLocalDay(value: Date): number {
  return new Date(value.getFullYear(), value.getMonth(), value.getDate()).getTime();
}

export function timePhaseAt(value: Date): TimePhase {
  const hour = value.getHours();
  return hour >= 22 || hour < 6 ? "night" : "day";
}

export function formatCurrentTime(value: Date): string {
  return (
    `${value.getMonth() + 1}月${value.getDate()}日 ` +
    `星期${WEEKDAYS[value.getDay()]} ${pad(value.getHours())}:${pad(value.getMinutes())}`
  );
}

export function formatMessageTime(timestamp: number, now: Date): string {
  if (!isValidTimestamp(timestamp)) return "时间未知";
  const value = new Date(timestamp * 1000);
  const clock = `${pad(value.getHours())}:${pad(value.getMinutes())}`;
  if (sameLocalDate(value, now)) return `今天 ${clock}`;
  const dayGap = Math.round(
    (startOfLocalDay(now) - startOfLocalDay(value)) / 86_400_000,
  );
  if (dayGap === 1) return `昨天 ${clock}`;
  return (
    `${value.getMonth() + 1}月${value.getDate()}日 ` +
    `周${WEEKDAYS[value.getDay()]} ${clock}`
  );
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
