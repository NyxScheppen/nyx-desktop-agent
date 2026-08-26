import { describe, expect, it } from "vitest";
import {
  ACTIVITY_STATUS_LABELS,
  ACTIVITY_TYPE_LABELS,
  DESIRE_STATUS_LABELS,
  DESIRE_TYPE_LABELS,
  ENCOUNTER_KIND_LABELS,
  ENERGY_LABELS,
  MEMORY_TYPE_LABELS,
  PERSONALITY_POLES,
  VALUES_POLES,
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
    ];
    for (const map of maps) {
      for (const value of Object.values(map)) {
        expect(typeof value).toBe("string");
        expect(value).not.toBe("");
      }
    }
  });

  it("Big Five / 三观双端语义均有 low/high 中文（无空值）", () => {
    for (const map of [PERSONALITY_POLES, VALUES_POLES]) {
      for (const pole of Object.values(map)) {
        expect(typeof pole.low).toBe("string");
        expect(pole.low).not.toBe("");
        expect(typeof pole.high).toBe("string");
        expect(pole.high).not.toBe("");
      }
    }
  });

  it("label() 命中键返回中文，未知键回退原值", () => {
    expect(label(DESIRE_TYPE_LABELS, "exploration")).toBe("发现");
    expect(label(DESIRE_TYPE_LABELS, "unknown_key")).toBe("unknown_key");
  });
});

describe("ENCOUNTER_KIND_LABELS", () => {
  it("三基础键中文映射", () => {
    expect(ENCOUNTER_KIND_LABELS.desire_chat).toBe("欲望搭话");
    expect(ENCOUNTER_KIND_LABELS.random_event).toBe("随机事件");
    expect(ENCOUNTER_KIND_LABELS.growth_moment).toBe("成长时刻");
  });

  it("rooted 有根遭遇", () => {
    expect(ENCOUNTER_KIND_LABELS.rooted).toBe("有根遭遇");
  });
});
