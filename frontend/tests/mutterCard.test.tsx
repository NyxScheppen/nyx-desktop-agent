import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";
import MutterCard from "../src/components/shell/MutterCard";
import { useMutterStore } from "../src/stores/mutterStore";

beforeEach(() => {
  useMutterStore.setState({ mutters: [] });
});

describe("MutterCard 碎碎念卡片", () => {
  it("显示最近 3 条碎碎念（最新在前）", () => {
    useMutterStore.setState({
      mutters: [
        { id: "m1", text: "第一条" },
        { id: "m2", text: "第二条" },
        { id: "m3", text: "第三条" },
        { id: "m4", text: "第四条" },
        { id: "m5", text: "第五条" },
      ],
    });

    render(<MutterCard />);

    const items = screen.getAllByRole("listitem");
    expect(items.map((n) => n.textContent)).toEqual(["第五条", "第四条", "第三条"]);
  });

  it("无碎碎念显示占位文案", () => {
    render(<MutterCard />);
    expect(screen.getByText("尼克斯安静地陪着你……")).toBeInTheDocument();
  });
});
