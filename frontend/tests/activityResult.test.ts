import { describe, expect, it } from "vitest";
import {
  activityAnnouncement,
  activitySubject,
  formatOutputBody,
  formatResult,
} from "../src/lib/activityResult";
import type { Activity } from "../src/types/api";

function activity(overrides: Partial<Activity>): Activity {
  return {
    id: "a1",
    type: "reading",
    schedule_block_id: "b1",
    status: "completed",
    progress: {},
    started_at: 1,
    ended_at: 2,
    ...overrides,
  };
}

describe("activitySubject", () => {
  it("取第一个非空字符串（filename/description/source）", () => {
    expect(activitySubject(activity({ progress: { filename: "书.txt" } }))).toBe("书.txt");
    expect(activitySubject(activity({ progress: { description: "探索某主题" } }))).toBe("探索某主题");
    expect(activitySubject(activity({ progress: { source: "/p/书.txt" } }))).toBe("/p/书.txt");
  });

  it("空 progress / 非字符串 / 空串 → null", () => {
    expect(activitySubject(activity({ progress: {} }))).toBeNull();
    expect(activitySubject(activity({ progress: { description: 5 } }))).toBeNull();
    expect(activitySubject(activity({ progress: { filename: "" } }))).toBeNull();
  });
});

describe("formatResult", () => {
  it("reading → {book} — {note}", () => {
    expect(
      formatResult(
        activity({ type: "reading", progress: { result: { book: "《小王子》", note: "关于驯服" } } }),
      ),
    ).toBe("《小王子》 — 关于驯服");
  });

  it("creation → {title} — {content}", () => {
    expect(
      formatResult(
        activity({ type: "creation", progress: { result: { title: "诗", content: "正文" } } }),
      ),
    ).toBe("诗 — 正文");
  });

  it("free_exploration → findings/notes 用 / 连接", () => {
    expect(
      formatResult(
        activity({
          type: "free_exploration",
          progress: { result: { findings: ["a", "b"], notes: ["n"] } },
        }),
      ),
    ).toBe("a / b / n");
  });

  it("未完成 / 无 result / 非 result 类型 → null", () => {
    expect(formatResult(activity({ status: "running", progress: { result: { book: "x" } } }))).toBeNull();
    expect(formatResult(activity({ progress: {} }))).toBeNull();
    expect(formatResult(activity({ type: "rest", progress: { result: { book: "x" } } }))).toBeNull();
  });
});

describe("formatOutputBody", () => {
  it("reading → note", () => {
    expect(
      formatOutputBody(
        activity({ progress: { result: { book: "《小王子》", note: "关于驯服" } } }),
      ),
    ).toBe("关于驯服");
  });

  it("creation → content", () => {
    expect(
      formatOutputBody(
        activity({ type: "creation", progress: { result: { title: "诗", content: "正文" } } }),
      ),
    ).toBe("正文");
  });

  it("free_exploration → findings/notes 用换行连接", () => {
    expect(
      formatOutputBody(
        activity({
          type: "free_exploration",
          progress: { result: { findings: ["a", "b"], notes: ["n"] } },
        }),
      ),
    ).toBe("a\nb\nn");
  });

  it("未完成 / 无对应字段 / 非 result 类型 → null", () => {
    expect(formatOutputBody(activity({ status: "running", progress: { result: { note: "x" } } }))).toBeNull();
    expect(formatOutputBody(activity({ progress: { result: {} } }))).toBeNull();
    expect(formatOutputBody(activity({ type: "rest", progress: { result: { note: "x" } } }))).toBeNull();
  });
});

describe("activityAnnouncement", () => {
  it("reading → 读完啦：…", () => {
    expect(
      activityAnnouncement(
        activity({ progress: { result: { book: "《小王子》", note: "关于驯服" } } }),
      ),
    ).toBe("读完啦：《小王子》 — 关于驯服");
  });

  it("creation → 创作完成：…", () => {
    expect(
      activityAnnouncement(
        activity({ type: "creation", progress: { result: { title: "诗", content: "正文" } } }),
      ),
    ).toBe("创作完成：诗 — 正文");
  });

  it("free_exploration → 探索收获：…", () => {
    expect(
      activityAnnouncement(
        activity({ type: "free_exploration", progress: { result: { findings: ["a"] } } }),
      ),
    ).toBe("探索收获：a");
  });

  it("无产出 / 未完成 → null", () => {
    expect(activityAnnouncement(activity({ progress: {} }))).toBeNull();
    expect(activityAnnouncement(activity({ status: "running", progress: { result: { book: "x" } } }))).toBeNull();
  });
});
