import { render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import ScrollArea from "../src/components/shell/ScrollArea";
import { useChatStore } from "../src/stores/chatStore";

function jsonResponse(body: unknown, ok = true, status = 200): Response {
  return { ok, status, json: async () => body } as Response;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

beforeEach(() => {
  useChatStore.getState().reset();
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse([])));
});

describe("ScrollArea", () => {
  it("渲染对话主舞台：消息列表，无模式切换按钮", () => {
    render(<ScrollArea />);
    // 消息列表容器常驻（空消息也渲染 .message-list）
    expect(document.querySelector(".message-list")).toBeTruthy();
    // 模式切换按钮已移除（记忆/笔记/对话不再出现）
    expect(screen.queryByText("记忆")).toBeNull();
    expect(screen.queryByText("笔记")).toBeNull();
    expect(screen.queryByText("对话")).toBeNull();
  });
});
