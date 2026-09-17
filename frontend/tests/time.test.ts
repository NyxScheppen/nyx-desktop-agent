import { describe, expect, it } from "vitest";
import type { ChatMessage } from "../src/stores/chatStore";
import {
  formatCurrentTime,
  formatMessageTime,
  shouldShowTimeDivider,
  timePhaseAt,
} from "../src/lib/time";

function at(year: number, month: number, day: number, hour: number, minute: number): Date {
  return new Date(year, month - 1, day, hour, minute, 0, 0);
}

function message(
  id: string,
  kind: ChatMessage["kind"],
  timestamp: number,
  correlationId = id,
): ChatMessage {
  return {
    id,
    role: kind === "message" ? "user" : "nyx",
    kind,
    content: id,
    correlation_id: correlationId,
    timestamp,
  };
}

describe("timePhaseAt", () => {
  it("06:00/22:00 切换昼夜", () => {
    expect(timePhaseAt(at(2026, 9, 17, 5, 59))).toBe("night");
    expect(timePhaseAt(at(2026, 9, 17, 6, 0))).toBe("day");
    expect(timePhaseAt(at(2026, 9, 17, 21, 59))).toBe("day");
    expect(timePhaseAt(at(2026, 9, 17, 22, 0))).toBe("night");
  });
});

describe("time labels", () => {
  it("顶栏显示日期、星期和分钟", () => {
    expect(formatCurrentTime(at(2026, 9, 17, 8, 5))).toBe("9月17日 星期四 08:05");
  });

  it("消息标签区分今天、昨天和更早", () => {
    const now = at(2026, 9, 18, 8, 0);
    expect(formatMessageTime(at(2026, 9, 18, 7, 1).getTime() / 1000, now)).toBe(
      "今天 07:01",
    );
    expect(formatMessageTime(at(2026, 9, 17, 19, 0).getTime() / 1000, now)).toBe(
      "昨天 19:00",
    );
    expect(formatMessageTime(at(2026, 9, 16, 9, 30).getTime() / 1000, now)).toBe(
      "9月16日 周三 09:30",
    );
  });
});

describe("shouldShowTimeDivider", () => {
  it("首条、跨日或间隔至少 30 分钟显示", () => {
    const base = at(2026, 9, 17, 10, 0).getTime() / 1000;
    const first = message("u1", "message", base);
    expect(shouldShowTimeDivider(first, null)).toBe(true);
    expect(shouldShowTimeDivider(message("u2", "message", base + 1799), first)).toBe(false);
    expect(shouldShowTimeDivider(message("u3", "message", base + 1800), first)).toBe(true);
    expect(
      shouldShowTimeDivider(
        message("u4", "message", at(2026, 9, 18, 0, 1).getTime() / 1000),
        first,
      ),
    ).toBe(true);
  });

  it("同 correlation 的 think/speak/ask 不重复", () => {
    const first = message("t1", "think", at(2026, 9, 17, 23, 59).getTime() / 1000, "c1");
    const speak = message("s1", "speak", at(2026, 9, 18, 0, 30).getTime() / 1000, "c1");
    expect(shouldShowTimeDivider(speak, first)).toBe(false);
  });
});
