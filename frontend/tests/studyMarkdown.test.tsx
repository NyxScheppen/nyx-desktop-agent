import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import StudyMarkdown from "../src/components/reading/StudyMarkdown";

describe("完整原文与来源链接", () => {
  it("保留完整引文并从书名和段号跳转原文", () => {
    const onSource = vi.fn();
    render(
      <StudyMarkdown
        text={
          "这是解释。[第206段](nyx-book:206)\n\n" +
          "> 小农的利益同一性并没有消失。\n>\n" +
          "> (1)\n> 缺的是全国性联系和政治组织。\n>\n" +
          "> [《路易·波拿巴的雾月十八日》·第 206 段](nyx-book:206)"
        }
        onSource={onSource}
      />,
    );
    expect(screen.getByRole("blockquote")).toHaveTextContent(
      "小农的利益同一性并没有消失。 (1) 缺的是全国性联系和政治组织。",
    );
    fireEvent.click(
      screen.getByRole("button", {
        name: "《路易·波拿巴的雾月十八日》·第 206 段",
      }),
    );
    expect(onSource).toHaveBeenCalledWith(206);
  });
});
