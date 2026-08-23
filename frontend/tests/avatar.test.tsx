import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";
import Avatar, { isNight } from "../src/components/inner/Avatar";
import { useAnnounceStore } from "../src/stores/announceStore";
import { useChatStore } from "../src/stores/chatStore";
import { useInnerLifeStore } from "../src/stores/innerLifeStore";

beforeEach(() => {
  useInnerLifeStore.setState({ current: null, loading: false, error: null });
  useChatStore.getState().reset();
  useAnnounceStore.setState({ items: [] });
});

describe("isNight 昼夜节律纯函数", () => {
  it("22–05 点为夜间，06–21 点为白天", () => {
    expect(isNight(22)).toBe(true);
    expect(isNight(0)).toBe(true);
    expect(isNight(5)).toBe(true);
    expect(isNight(6)).toBe(false);
    expect(isNight(12)).toBe(false);
    expect(isNight(21)).toBe(false);
  });
});

describe("Avatar 红点通知", () => {
  it("unreadProactive=true 显示徽标，点击清除", () => {
    useChatStore.setState({ unreadProactive: true });
    render(<Avatar />);
    const badge = screen.getByRole("button", { name: "小狐狸我有话对你说" });
    expect(badge).toBeInTheDocument();
    fireEvent.click(badge);
    expect(useChatStore.getState().unreadProactive).toBe(false);
  });
});

describe("Avatar 戳立绘", () => {
  it("戳一下冒害羞短语，连戳 5 次冒生气短语", () => {
    render(<Avatar />);
    const avatar = screen.getByTitle("戳一戳");
    fireEvent.click(avatar);
    expect(useAnnounceStore.getState().items[0]?.text).toBe("呀！");
    fireEvent.click(avatar);
    fireEvent.click(avatar);
    fireEvent.click(avatar);
    fireEvent.click(avatar); // 第 5 次
    expect(useAnnounceStore.getState().items[4]?.text).toBe("不要再戳了啦！");
  });
});
