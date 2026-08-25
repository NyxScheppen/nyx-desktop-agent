import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import EncounterCard from "../src/components/encounter/EncounterCard";
import { useEncounterStore } from "../src/stores/encounterStore";

beforeEach(() => useEncounterStore.getState().reset());

describe("EncounterCard", () => {
  it("current null 不渲染", () => {
    const { container } = render(<EncounterCard />);
    expect(container.querySelector(".encounter-card")).toBeNull();
  });

  it("渲染文本 + 选项按钮，点击调 choose", () => {
    const chooseSpy = vi.spyOn(useEncounterStore.getState(), "choose").mockResolvedValue(undefined);
    useEncounterStore.setState({
      current: { encounter_id: "enc1", kind: "random_event", text: "开场", options: [
        { index: 0, text: "走过去" }, { index: 1, text: "停下" },
      ] },
    });

    render(<EncounterCard />);
    expect(screen.getByText("开场")).toBeTruthy();
    fireEvent.click(screen.getByText("走过去"));

    expect(chooseSpy).toHaveBeenCalledWith("enc1", 0);
  });

  it("choosing 期间选项禁用", () => {
    useEncounterStore.setState({
      current: { encounter_id: "enc1", kind: "random_event", text: "开场", options: [{ index: 0, text: "走" }] },
      choosing: true,
    });
    render(<EncounterCard />);
    expect((screen.getByText("走") as HTMLButtonElement).disabled).toBe(true);
  });
});
