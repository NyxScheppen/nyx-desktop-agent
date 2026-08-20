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
    useInnerLifeStore.setState({ current: null, loading: false, error: null });
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

  it("BigFiveChart 按 personality 渲染中文标签 + 数值", () => {
    render(<BigFiveChart personality={currentFixture.personality} />);
    expect(screen.getByText("开放性")).toBeInTheDocument();
    expect(screen.getByText("神经质")).toBeInTheDocument();
    expect(screen.getByText("2")).toBeInTheDocument(); // extraversion 唯一值
    expect(screen.getByText("7")).toBeInTheDocument(); // neuroticism 唯一值
  });

  it("ValuesChart 按 values 渲染中文标签 + 数值", () => {
    render(<ValuesChart values={currentFixture.values} />);
    expect(screen.getByText("对人类的态度")).toBeInTheDocument();
    expect(screen.getByText("乐观")).toBeInTheDocument();
    expect(screen.getByText("9")).toBeInTheDocument(); // altruism 唯一值
    expect(screen.getByText("5")).toBeInTheDocument(); // optimism 唯一值
  });

  it("ValenceArousalPlot 渲染不崩（坐标不做断言）", () => {
    const { container } = render(<ValenceArousalPlot valence={0.1} arousal={0.2} />);
    expect(container.querySelector("svg.va-plot")).not.toBeNull();
  });

  it("InnerStatePanel current=null → 整体占位，不渲染子组件", () => {
    render(<InnerStatePanel />);
    expect(screen.getByText("等待核心服务连接…")).toBeInTheDocument();
    expect(screen.queryByText("开放性")).not.toBeInTheDocument();
  });

  it("InnerStatePanel current 非 null → 渲染各子组件字段", () => {
    useInnerLifeStore.setState({ current: currentFixture });
    render(<InnerStatePanel />);
    expect(screen.getByText("精力充沛")).toBeInTheDocument(); // EnergyBar
    expect(screen.getByText("开放性")).toBeInTheDocument(); // BigFiveChart
    expect(screen.getByText("对人类的态度")).toBeInTheDocument(); // ValuesChart
  });

  it("InnerStatePanel error 非 null → 顶部红字一行", () => {
    useInnerLifeStore.setState({ error: "连接失败" });
    render(<InnerStatePanel />);
    expect(screen.getByText("连接失败")).toHaveClass("inner-state-panel__error");
  });
});
