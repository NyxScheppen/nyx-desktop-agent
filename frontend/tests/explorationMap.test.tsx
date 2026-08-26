import { render, screen, fireEvent } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import ExplorationMap from "../src/components/exploration/ExplorationMap";
import { useExplorationStore } from "../src/stores/explorationStore";
import type { ExplorationDecision } from "../src/types/api";

const decision: ExplorationDecision = {
  kind: "choose",
  floor: 2,
  energy: 62,
  focus: "量子退相干",
  nodes: [
    { name: "维基·量子退相干", url: "", kind: "real", snippet: "退相干是……", may_encounter: false },
    { name: "arXiv·拓扑纠错", url: "", kind: "real", snippet: "拓扑保护……", may_encounter: true },
    { name: "本地·无结果", url: "", kind: "dead_end", snippet: "", may_encounter: false },
  ],
};

beforeEach(() => {
  useExplorationStore.setState({
    decision, activityId: "a1", autopilot: false, choosing: false, error: null,
    history: [{ floor: 1, name: "维基·量子计算", kind: "real" }],
  });
});

describe("ExplorationMap", () => {
  it("渲染 HUD（目标/深度）+ 节点 + 安全房", () => {
    render(<ExplorationMap />);
    expect(screen.getByText("量子退相干")).toBeTruthy();       // focus
    expect(screen.getAllByText("第 2 层").length).toBeGreaterThan(0); // 深度
    expect(screen.getByText("维基·量子退相干")).toBeTruthy();  // 节点
    expect(screen.getByText("休息整理")).toBeTruthy();          // 安全房
  });

  it("点节点 → choose('node:0')", () => {
    const spy = vi.spyOn(useExplorationStore.getState(), "choose").mockResolvedValue(undefined);
    render(<ExplorationMap />);
    fireEvent.click(screen.getByText("维基·量子退相干"));
    expect(spy).toHaveBeenCalledWith("node:0");
    spy.mockRestore();
  });

  it("无 decision → 渲染「出门探索」+ 点击调 start", () => {
    useExplorationStore.setState({ decision: null, activityId: null });
    const spy = vi.spyOn(useExplorationStore.getState(), "start").mockResolvedValue(undefined);
    render(<ExplorationMap />);
    expect(screen.getByText("出门探索")).toBeTruthy();
    fireEvent.click(screen.getByText("出门探索"));
    expect(spy).toHaveBeenCalled();
    spy.mockRestore();
  });

  it("展开地图显示已走过楼层 + 进过节点", () => {
    render(<ExplorationMap />);
    fireEvent.click(screen.getByText(/展开地图/));
    expect(screen.getByText("维基·量子计算")).toBeTruthy(); // 第 1 层足迹
  });

  it("点「下楼」→ choose('descend')", () => {
    const spy = vi.spyOn(useExplorationStore.getState(), "choose").mockResolvedValue(undefined);
    render(<ExplorationMap />);
    fireEvent.click(screen.getByText(/下楼/));
    expect(spy).toHaveBeenCalledWith("descend");
    spy.mockRestore();
  });
});
