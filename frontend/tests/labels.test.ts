import { describe, expect, it } from "vitest";
import {
  DESIRE_TYPE_LABELS,
  label,
} from "../src/lib/labels";

describe("labels 枚举中文化映射", () => {
  it("label() 命中键返回中文，未知键回退原值", () => {
    expect(label(DESIRE_TYPE_LABELS, "exploration")).toBe("发现");
    expect(label(DESIRE_TYPE_LABELS, "unknown_key")).toBe("unknown_key");
  });
});

