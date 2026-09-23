import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import RightDock from "../src/components/shell/RightDock";

describe("RightDock", () => {
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
});
