import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import RightDock from "../src/components/shell/RightDock";

describe("RightDock", () => {
  it("桌面端使用底部印章收回桌宠", () => {
    const onReturnToPet = vi.fn();
    render(<RightDock view="chat" onSwitch={vi.fn()} onOpenSettings={vi.fn()} onReturnToPet={onReturnToPet} />);
    fireEvent.click(screen.getByRole("button", { name: "收回桌宠" }));
    expect(onReturnToPet).toHaveBeenCalledOnce();
  });
  it("创作入口切换到 creation 视图", () => {
    const onSwitch = vi.fn();
    render(
      <RightDock
        view="activity"
        onSwitch={onSwitch}
        onOpenSettings={vi.fn()}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "创作" }));

    expect(onSwitch).toHaveBeenCalledWith("creation");
  });

  it("评估入口切换到 eval 视图", () => {
    const onSwitch = vi.fn();
    render(
      <RightDock
        view="activity"
        onSwitch={onSwitch}
        onOpenSettings={vi.fn()}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "评估" }));

    expect(onSwitch).toHaveBeenCalledWith("eval");
  });

  it("任务入口切换到任务页", () => {
    const onSwitch = vi.fn();
    render(
      <RightDock
        view="memory"
        onSwitch={onSwitch}
        onOpenSettings={vi.fn()}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "任务" }));

    expect(onSwitch).toHaveBeenCalledWith("tasks");
  });
});
