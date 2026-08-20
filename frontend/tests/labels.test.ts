import { describe, expect, it } from "vitest";
import {
  ACTIVITY_STATUS_LABELS,
  ACTIVITY_TYPE_LABELS,
  DESIRE_STATUS_LABELS,
  DESIRE_TYPE_LABELS,
  ENERGY_LABELS,
  MEMORY_TYPE_LABELS,
  PERSONALITY_LABELS,
  VALUES_LABELS,
  label,
} from "../src/lib/labels";

describe("labels 枚举中文化映射", () => {
  it("用户示例 exploration → 发现", () => {
    expect(DESIRE_TYPE_LABELS.exploration).toBe("发现");
  });

  it("各枚举键均有中文映射（无 undefined 值）", () => {
    const maps = [
      ENERGY_LABELS,
      DESIRE_TYPE_LABELS,
      DESIRE_STATUS_LABELS,
      ACTIVITY_TYPE_LABELS,
      ACTIVITY_STATUS_LABELS,
      MEMORY_TYPE_LABELS,
      PERSONALITY_LABELS,
      VALUES_LABELS,
    ];
    for (const map of maps) {
      for (const value of Object.values(map)) {
        expect(typeof value).toBe("string");
        expect(value).not.toBe("");
      }
    }
  });

  it("label() 命中键返回中文，未知键回退原值", () => {
    expect(label(DESIRE_TYPE_LABELS, "exploration")).toBe("发现");
    expect(label(DESIRE_TYPE_LABELS, "unknown_key")).toBe("unknown_key");
  });
});
