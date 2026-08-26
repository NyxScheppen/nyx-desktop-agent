import { describe, expect, it } from "vitest";
import {
  activityAnnouncement,
  activityStatusText,
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

  it("free_exploration → summary 与 core_discovery 用 — 连接", () => {
    expect(
      formatResult(
        activity({
          type: "free_exploration",
          progress: { result: { summary: "弄懂了退相干", core_discovery: "环境纠缠抹去相干性" } },
        }),
      ),
    ).toBe("弄懂了退相干 — 环境纠缠抹去相干性");
  });

  it("free_exploration → 无 core_discovery 只留 summary", () => {
    expect(
      formatResult(
        activity({ type: "free_exploration", progress: { result: { summary: "翻了翻量子资料" } } }),
      ),
    ).toBe("翻了翻量子资料");
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

  it("free_exploration → core_discovery + knowledge 逐条", () => {
    expect(
      formatOutputBody(
        activity({
          type: "free_exploration",
          progress: {
            result: {
              core_discovery: "环境纠缠抹去相干性",
              knowledge: [
                { topic: "退相干", content: "环境纠缠" },
                { topic: "纠错", content: "拓扑保护" },
              ],
            },
          },
        }),
      ),
    ).toBe("核心发现：环境纠缠抹去相干性\n【退相干】环境纠缠\n【纠错】拓扑保护");
  });

  it("free_exploration → 无 knowledge 只留 summary", () => {
    expect(
      formatOutputBody(
        activity({ type: "free_exploration", progress: { result: { summary: "翻了翻" } } }),
      ),
    ).toBe("翻了翻");
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
        activity({ type: "free_exploration", progress: { result: { summary: "弄懂了退相干" } } }),
      ),
    ).toBe("探索收获：弄懂了退相干");
  });

  it("无产出 / 未完成 → null", () => {
    expect(activityAnnouncement(activity({ progress: {} }))).toBeNull();
    expect(activityAnnouncement(activity({ status: "running", progress: { result: { book: "x" } } }))).toBeNull();
  });
});

describe("activityStatusText", () => {
  it("null → 空闲；六类活动文案", () => {
    expect(activityStatusText(null)).toBe("空闲");
    expect(activityStatusText(activity({ type: "reading", progress: { filename: "书.txt" } }))).toBe("在读《书.txt》");
    expect(activityStatusText(activity({ type: "free_exploration", progress: { description: "某主题" } }))).toBe("在探索「某主题」");
    expect(activityStatusText(activity({ type: "creation" }))).toBe("在创作");
    expect(activityStatusText(activity({ type: "observe_user" }))).toBe("在观察你");
    expect(activityStatusText(activity({ type: "idle_reflection" }))).toBe("在静默反思");
    expect(activityStatusText(activity({ type: "rest" }))).toBe("在休息");
  });
});
