import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";
import BigFiveChart from "../src/components/inner/BigFiveChart";
import EmotionSprite from "../src/components/inner/EmotionSprite";
import EnergyBar from "../src/components/inner/EnergyBar";
import InnerStatePanel from "../src/components/inner/InnerStatePanel";
import ValenceArousalPlot from "../src/components/inner/ValenceArousalPlot";
import ValuesChart from "../src/components/inner/ValuesChart";
import { useInnerLifeStore } from "../src/stores/innerLifeStore";
import type { CurrentState } from "../src/types/api";

const currentFixture: CurrentState = {
  valence: 0.1,
  arousal: 0.2,
  emotion: "neutral",
  personality: {
    openness: 8,
    conscientiousness: 8,
    extraversion: 2,
    agreeableness: 6,
    neuroticism: 7,
  },
  values: {
    attitude_to_human: 8,
    ai_identity_acceptance: 6,
    altruism: 9,
    optimism: 5,
  },
  energy: 100,
  energy_state: "energetic",
  current_activity: null,
  active_desires: [],
};

describe("inner 面板子组件", () => {
  beforeEach(() => {
    useInnerLifeStore.setState({ current: null, error: null });
  });

  it("EnergyBar 按 energy_state 渲染中文文案", () => {
    render(<EnergyBar energy={42} energy_state="tired" />);
    expect(screen.getByText("疲惫")).toBeInTheDocument();
  });

  it("EmotionSprite 按 emotion 选图文件名", () => {
    useInnerLifeStore.setState({ current: { ...currentFixture, emotion: "happy" } });
    render(<EmotionSprite />);
    const img = screen.getByAltText("happy") as HTMLImageElement;
    expect(img.src).toContain("happy");
  });

  it("EmotionSprite 表情图 draggable=false（防原生图片拖拽抢占圆圈拖拽）", () => {
    useInnerLifeStore.setState({ current: { ...currentFixture, emotion: "neutral" } });
    render(<EmotionSprite />);
    expect(screen.getByAltText("neutral")).toHaveAttribute("draggable", "false");
  });

  it("BigFiveChart 按 personality 渲染双端语义", () => {
    render(<BigFiveChart personality={currentFixture.personality} />);
    expect(screen.getByText("保守")).toBeInTheDocument(); // openness 低端
    expect(screen.getByText("开放")).toBeInTheDocument(); // openness 高端
    expect(screen.getByText("情绪稳定")).toBeInTheDocument(); // neuroticism 低端
    expect(screen.getByText("敏感")).toBeInTheDocument(); // neuroticism 高端
  });

  it("ValuesChart 按 values 渲染双端语义", () => {
    render(<ValuesChart values={currentFixture.values} />);
    expect(screen.getByText("疏离")).toBeInTheDocument(); // attitude_to_human 低端
    expect(screen.getByText("亲近")).toBeInTheDocument(); // attitude_to_human 高端
    expect(screen.getByText("悲观")).toBeInTheDocument(); // optimism 低端
    expect(screen.getByText("乐观")).toBeInTheDocument(); // optimism 高端
  });

  it("ValenceArousalPlot 渲染不崩（坐标不做断言）", () => {
    const { container } = render(<ValenceArousalPlot valence={0.1} arousal={0.2} />);
    expect(container.querySelector("svg.va-plot")).not.toBeNull();
  });

  it("ValenceArousalPlot 区域标签对齐后端 6 档（含害羞/担忧，无旧「低落」）", () => {
    render(<ValenceArousalPlot valence={0.1} arousal={0.2} />);
    for (const label of ["开心", "生气", "担忧", "悲伤", "害羞", "平静"]) {
      expect(screen.getByText(label)).toBeInTheDocument();
    }
    expect(screen.queryByText("低落")).not.toBeInTheDocument(); // 旧错误标签已移除
  });

  it("InnerStatePanel current=null → 整体占位，不渲染子组件", () => {
    render(<InnerStatePanel />);
    expect(screen.getByText("等待核心服务连接…")).toBeInTheDocument();
    expect(screen.queryByText("开放")).not.toBeInTheDocument();
  });

  it("InnerStatePanel current 非 null → 渲染各子组件字段", () => {
    useInnerLifeStore.setState({ current: currentFixture });
    render(<InnerStatePanel />);
    expect(screen.getByText("精力充沛")).toBeInTheDocument(); // EnergyBar
    expect(screen.getByText("开放")).toBeInTheDocument(); // BigFiveChart
    expect(screen.getByText("亲近")).toBeInTheDocument(); // ValuesChart
  });

  it("InnerStatePanel error 非 null → 顶部红字一行", () => {
    useInnerLifeStore.setState({ error: "连接失败" });
    render(<InnerStatePanel />);
    expect(screen.getByText("连接失败")).toHaveClass("inner-state-panel__error");
  });
});
