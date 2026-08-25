import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import ScrollArea from "../src/components/shell/ScrollArea";
import { useChatStore } from "../src/stores/chatStore";
import { useMemoryStore } from "../src/stores/memoryStore";
import { useReadingNotesStore } from "../src/stores/readingNotesStore";

function jsonResponse(body: unknown, ok = true, status = 200): Response {
  return { ok, status, json: async () => body } as Response;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

beforeEach(() => {
  useChatStore.getState().reset();
  useMemoryStore.setState({ data: null, error: null });
  useReadingNotesStore.setState({ notes: null, loading: false, error: null });
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse([])));
});

describe("ScrollArea", () => {
  it("默认对话模式，可切到记忆/笔记", () => {
    render(<ScrollArea />);
    // 左下角三个模式按钮
    expect(screen.getByText("对话")).toBeTruthy();
    fireEvent.click(screen.getByText("记忆"));
    fireEvent.click(screen.getByText("笔记"));
    // 切回对话
    fireEvent.click(screen.getByText("对话"));
  });
});
