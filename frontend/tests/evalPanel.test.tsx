import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import EvalPanel from "../src/components/panels/EvalPanel";
import { useEvalStore } from "../src/stores/evalStore";
import type { EvalRecord } from "../src/types/api";

function rec(
  id: string,
  output_type: string,
  oocKeyword: number,
): EvalRecord {
  return {
    id,
    created_at: 1,
    call_id: "c1",
    module: "expression",
    output_type,
    model: "m",
    correlation_id: "k",
    ooc_keyword: oocKeyword,
    ooc_embed: null,
    prompt_tokens: 5,
    completion_tokens: 2,
  };
}

describe("EvalPanel LLM 调用 / token 面板", () => {
  beforeEach(() => {
    // 阻断 mount 时的 refresh() 真实 fetch（本测直接 setState 验渲染）
    vi.spyOn(useEvalStore.getState(), "refresh").mockResolvedValue(undefined);
    useEvalStore.setState({
      prompts: {},
      promptLoading: {},
      promptErrors: {},
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("渲染总 token 与最近调用（类型中文化 + OOC 分 + token）", () => {
    useEvalStore.setState({
      records: [rec("e1", "speak", 1.0), rec("e2", "think", 0.8)],
      stats: { total_tokens: 14, prompt_tokens: 10, completion_tokens: 4 },
      error: null,
    });

    render(<EvalPanel />);

    expect(
      screen.getByRole("heading", { name: "LLM 调用 / token" }),
    ).toBeInTheDocument();
    expect(screen.getByText("总 token")).toBeInTheDocument();
    expect(screen.getByText(/14（prompt 10 \/ completion 4）/)).toBeInTheDocument();
    // output_type → 中文（OUTPUT_TYPE_LABELS）
    expect(screen.getByText("对外")).toBeInTheDocument();
    expect(screen.getByText("内心")).toBeInTheDocument();
    // OOC 分 + token 消耗
    expect(screen.getByText(/OOC 1\.00/)).toBeInTheDocument();
    expect(screen.getByText(/OOC 0\.80/)).toBeInTheDocument();
    expect(screen.getAllByText(/5\+2 token/)).toHaveLength(2);
  });

  it("无记录时显示空态", () => {
    useEvalStore.setState({
      records: [],
      stats: { total_tokens: 0, prompt_tokens: 0, completion_tokens: 0 },
      error: null,
    });

    render(<EvalPanel />);

    expect(screen.getByText("还没有 LLM 调用记录")).toBeInTheDocument();
  });

  it("records=null 显示等待态", () => {
    useEvalStore.setState({ records: null, stats: null, error: null });

    render(<EvalPanel />);

    expect(screen.getByText("等待核心服务连接…")).toBeInTheDocument();
  });

  it("展开记录时懒加载并安全展示完整 prompt", () => {
    const loadPrompt = vi.fn().mockResolvedValue(undefined);
    useEvalStore.setState({
      records: [rec("e1", "speak", 1.0)],
      stats: { total_tokens: 7, prompt_tokens: 5, completion_tokens: 2 },
      error: null,
      prompts: {
        e1: [
          { role: "system", content: "设定\n第二行" },
          { role: "user", content: "<script>alert(1)</script>" },
        ],
      },
      loadPrompt,
    });
    const { container } = render(<EvalPanel />);
    const summary = screen.getByText("对外").closest("summary");
    expect(summary).not.toBeNull();

    const details = summary?.closest("details") as HTMLDetailsElement;
    details.open = true;
    fireEvent(details, new Event("toggle", { bubbles: true }));

    expect(loadPrompt).toHaveBeenCalledWith("e1");
    expect(screen.getByText("system")).toBeInTheDocument();
    expect(container.querySelector("pre")?.textContent).toBe("设定\n第二行");
    expect(screen.getByText("<script>alert(1)</script>")).toBeInTheDocument();
    expect(container.querySelector("script")).toBeNull();
  });

  it("区分未保存、空 prompt、加载和错误状态", () => {
    useEvalStore.setState({
      records: [
        rec("legacy", "speak", 1.0),
        rec("empty", "think", 1.0),
        rec("loading", "tool", 1.0),
        rec("failed", "desire", 1.0),
      ],
      stats: { total_tokens: 0, prompt_tokens: 0, completion_tokens: 0 },
      prompts: { legacy: null, empty: [] },
      promptLoading: { loading: true },
      promptErrors: { failed: "fetch failed" },
    });

    render(<EvalPanel />);

    expect(screen.getByText("该记录未保存 prompt")).toBeInTheDocument();
    expect(screen.getByText("prompt 为空")).toBeInTheDocument();
    expect(screen.getByText("正在加载 prompt…")).toBeInTheDocument();
    expect(screen.getByText("fetch failed")).toBeInTheDocument();
  });
});
