import { render, screen } from "@testing-library/react";
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
});
