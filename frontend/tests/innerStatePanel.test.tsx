import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";
import EmotionSprite from "../src/components/inner/EmotionSprite";
import InnerStatePanel from "../src/components/inner/InnerStatePanel";
import ValenceArousalPlot from "../src/components/inner/ValenceArousalPlot";
import { useInnerLifeStore } from "../src/stores/innerLifeStore";
import type { CurrentState } from "../src/types/api";

const currentFixture: CurrentState = {
  valence: 0.1,
  arousal: 0.2,
  emotion: "neutral",
  inner_document: { topics: [{ section: "identity", id: "self-understanding", title: "自我理解", content: "我正在认识自己。" }], revision: 1, updated_at: 1000 },
  energy: 100,
  energy_state: "energetic",
  current_activity: null,
  active_desires: [],
};

describe("inner 面板子组件", () => {
  beforeEach(() => {
    useInnerLifeStore.setState({ current: null, error: null });
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
    expect(screen.getAllByText("自我理解")).toHaveLength(2);
    expect(screen.getByText("我正在认识自己。")).toBeInTheDocument();
    expect(screen.getByRole("article", { name: "当前内在文档" })).toBeInTheDocument();
  });

  it("InnerStatePanel 显示连接错误", () => {
    useInnerLifeStore.setState({ error: "连接失败" });
    render(<InnerStatePanel />);
    expect(screen.getByText("连接失败")).toBeInTheDocument();
  });
});
