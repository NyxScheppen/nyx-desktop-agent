import { render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import DesiresPanel from "../src/components/panels/DesiresPanel";
import { useDesireStore } from "../src/stores/desireStore";
import type { DesireState, ShortTermDesire } from "../src/types/api";

function std(
  id: string,
  description: string,
  status: ShortTermDesire["status"],
): ShortTermDesire {
  return {
    id,
    created_at: 1000,
    type: "interaction",
    strength: 0.8,
    description,
    goal: null,
    retry_count: 0,
    status,
  };
}

const mixed: DesireState = {
  values: [
    {
      type: "interaction",
      value: 0.5,
      expression_weight: 0.1,
      suppression_threshold: 0.9,
      updated_at: 1000,
    },
  ],
  short_term: [
    std("p", "待定欲望", "pending"),
    std("a", "进行中欲望", "active"),
    std("s", "被抑制欲望", "suppressed"),
    std("sat", "已满足欲望", "satisfied"),
    std("e", "已过期欲望", "expired"),
  ],
  long_term: [],
};

describe("DesiresPanel 短期欲望过滤终态", () => {
  beforeEach(() => {
    // 阻断 mount 时的 refresh() 真实 fetch（本测只验过滤渲染，不验数据拉取）
    vi.spyOn(useDesireStore.getState(), "refresh").mockResolvedValue(undefined);
    useDesireStore.setState({ data: mixed, error: null });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("渲染活队列（pending/active/suppressed），过滤 expired/satisfied", () => {
    render(<DesiresPanel />);
    for (const desc of ["待定欲望", "进行中欲望", "被抑制欲望"]) {
      expect(screen.getByText(new RegExp(desc))).toBeInTheDocument();
    }
    expect(screen.queryByText(/已满足欲望/)).not.toBeInTheDocument();
    expect(screen.queryByText(/已过期欲望/)).not.toBeInTheDocument();
  });

  it("短期欲望全是终态 → 不渲染「短期欲望」空区块", () => {
    useDesireStore.setState({
      data: {
        values: [],
        short_term: [
          std("sat", "已满足欲望", "satisfied"),
          std("e", "已过期欲望", "expired"),
        ],
        long_term: [],
      },
      error: null,
    });
    render(<DesiresPanel />);
    expect(screen.queryByText("短期欲望")).not.toBeInTheDocument();
  });
});
