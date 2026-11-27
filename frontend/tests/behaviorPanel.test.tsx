import { act, render, screen } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import BehaviorPanel from "../src/components/panels/BehaviorPanel";
import { useBehaviorStore, type ResearchRun } from "../src/stores/behaviorStore";

it("groups autonomous retries and renders their actions only in the trace", () => {
  const before = useBehaviorStore.getState();
  const action = { name: "prune", text: "同名球员来自无关的搜索结果" };
  const latest: ResearchRun = {
    id: "latest",
    question: "小说里的莱恩哈特是谁？",
    correlation_id: "desire-1",
    status: "completed",
    origin: "desire",
    created_at: 2000,
    result: { outcome: "exhausted", actions: [action] },
    activity: { progress: { exploration: { actions: [action] } } },
  };
  useBehaviorStore.setState({
    runs: [
      latest,
      {
        ...latest, id: "first", created_at: 1000, status: "failed",
        result: { error: "余额不足" }, activity: { progress: { exploration: {} } },
      },
    ],
    refresh: vi.fn().mockResolvedValue(undefined),
  });
  try {
    render(<BehaviorPanel />);
    expect(screen.getAllByText(/小说里的莱恩哈特是谁/)).toHaveLength(1);
    expect(screen.getByText(/2 次尝试/)).toHaveTextContent("未解决");
    expect(screen.getAllByText(/同名球员来自无关的搜索结果/)).toHaveLength(1);
    expect(screen.getByText(/余额不足/)).toBeInTheDocument();
  } finally {
    act(() => useBehaviorStore.setState(before));
  }
});
