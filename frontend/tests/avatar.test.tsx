import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import Avatar, { clampAvatarPos } from "../src/components/inner/Avatar";
import EmotionSprite from "../src/components/inner/EmotionSprite";
import { useCharacterStore } from "../src/stores/characterStore";
import { useConnectionStore } from "../src/stores/connectionStore";
import { useChatStore } from "../src/stores/chatStore";
import { useInnerLifeStore } from "../src/stores/innerLifeStore";
import { useSettingsStore } from "../src/stores/settingsStore";

const desktopWindowMocks = vi.hoisted(() => ({
  isTauriRuntime: vi.fn(() => false),
  startNativeWindowDrag: vi.fn(async () => {}),
}));

vi.mock("../src/lib/desktopWindow", () => desktopWindowMocks);

beforeEach(() => {
  useInnerLifeStore.setState({ current: null, error: null });
  useChatStore.getState().reset();
  useSettingsStore.setState({ circleSize: "large", avatarPos: null });
  desktopWindowMocks.isTauriRuntime.mockReturnValue(false);
  desktopWindowMocks.startNativeWindowDrag.mockClear();
});

describe("clampAvatarPos 拖拽坐标夹取纯函数", () => {
  it("视口内坐标原样返回", () => {
    expect(clampAvatarPos({ x: 100, y: 200 }, 1024, 768, 144)).toEqual({ x: 100, y: 200 });
  });

  it("越界坐标夹回 [0, viewport-size]", () => {
    // size=144：右下界 x<=1024-144=880、y<=768-144=624
    expect(clampAvatarPos({ x: -10, y: -5 }, 1024, 768, 144)).toEqual({ x: 0, y: 0 });
    expect(clampAvatarPos({ x: 2000, y: 2000 }, 1024, 768, 144)).toEqual({ x: 880, y: 624 });
  });

  it("size 决定右/下边界（小档 96 夹得更远）", () => {
    expect(clampAvatarPos({ x: 2000, y: 2000 }, 1024, 768, 96)).toEqual({ x: 928, y: 672 });
  });
});

describe("Avatar 红点通知", () => {
  it("unreadProactive=true 显示徽标，点击清除", () => {
    useChatStore.setState({ unreadProactive: true });
    render(<Avatar night={false} />);
    const badge = screen.getByRole("button", { name: "小狐狸我有话对你说" });
    expect(badge).toBeInTheDocument();
    fireEvent.click(badge);
    expect(useChatStore.getState().unreadProactive).toBe(false);
  });
});

describe("Avatar 桌宠入口", () => {
  it("loads protected card images with Bearer and revokes the blob on unmount", async () => {
    useConnectionStore.setState({ token: "asset-token", required: false });
    useCharacterStore.setState({ registry: { current_id: "alice", selection_revision: 1, limit: 4,
      characters: [{ id: "alice", name: "Alice", status: "RUNNING", error: null, sync_error: null,
        assets: { neutral: "/api/characters/alice/assets/neutral" } }] } });
    const fetch = vi.fn(async (_url: RequestInfo | URL, _init?: RequestInit) => new Response(new Blob(["image"])));
    vi.stubGlobal("fetch", fetch);
    const create = vi.fn(() => "blob:protected-image");
    const revoke = vi.fn();
    vi.stubGlobal("URL", Object.assign(URL, { createObjectURL: create, revokeObjectURL: revoke }));
    const view = render(<EmotionSprite emotion="neutral" />);
    try {
      await waitFor(() => expect(screen.getByRole("img")).toHaveAttribute("src", "blob:protected-image"));
      expect(new Headers(fetch.mock.calls[0][1]?.headers).get("Authorization")).toBe("Bearer asset-token");
      view.unmount();
      expect(revoke).toHaveBeenCalledWith("blob:protected-image");
    } finally {
      view.unmount();
      useCharacterStore.setState({ registry: null });
      useConnectionStore.setState({ token: "", required: false });
      vi.unstubAllGlobals();
    }
  });
  it("uses an anonymous CORS request for card images and a name placeholder for a role without assets", () => {
    const registry = { current_id: "alice", selection_revision: 1, limit: 4,
      characters: [{ id: "alice", name: "Alice", status: "RUNNING" as const,
        error: null, sync_error: null, assets: { neutral: "/api/characters/alice/assets/neutral" } }] };
    useCharacterStore.setState({ registry });
    const view = render(<EmotionSprite emotion="neutral" />);
    try {
      const picture = screen.getByRole("img", { name: "neutral" });
      expect(picture).toHaveAttribute("src", "/api/characters/alice/assets/neutral");
      expect(picture).toHaveAttribute("crossorigin", "anonymous");
      act(() => useCharacterStore.setState({ registry: { ...registry,
        characters: [{ ...registry.characters[0], assets: {} }] } }));
      expect(screen.queryByRole("img")).not.toBeInTheDocument();
      expect(screen.getByText("Alice")).toBeInTheDocument();
    } finally {
      view.unmount();
      useCharacterStore.setState({ registry: null });
    }
  });

  it("单击和双击分别触发菜单与完整桌面端回调", () => {
    const onActivate = vi.fn();
    const onDoubleClick = vi.fn();
    render(<Avatar night={false} onActivate={onActivate} onDoubleClick={onDoubleClick} />);
    const avatar = screen.getByTitle("Nyx 头像");
    fireEvent.click(avatar);
    fireEvent.doubleClick(avatar);
    expect(onActivate).toHaveBeenCalledTimes(1);
    expect(onDoubleClick).toHaveBeenCalledTimes(1);
  });

  it("拖拽越过阈值后不把释放动作当成点击", () => {
    const onActivate = vi.fn();
    render(<Avatar night={false} onActivate={onActivate} />);
    const avatar = screen.getByTitle("Nyx 头像");
    Object.defineProperty(avatar, "getBoundingClientRect", {
      value: () => ({ left: 0, top: 0, right: 144, bottom: 144, width: 144, height: 144 }),
    });
    Object.defineProperty(avatar, "setPointerCapture", { value: vi.fn() });
    const dispatchPointer = (type: string, clientX: number, clientY: number) => {
      const event = new Event(type, { bubbles: true });
      Object.defineProperties(event, {
        pointerId: { value: 1 },
        clientX: { value: clientX },
        clientY: { value: clientY },
      });
      fireEvent(avatar, event);
    };

    dispatchPointer("pointerdown", 10, 10);
    dispatchPointer("pointermove", 20, 10);
    dispatchPointer("pointerup", 20, 10);
    fireEvent.click(avatar);

    expect(onActivate).not.toHaveBeenCalled();
  });

  it("完整端在 Tauri 中只移动头像，桌宠态才移动原生窗口", () => {
    desktopWindowMocks.isTauriRuntime.mockReturnValue(true);
    const { rerender } = render(<Avatar night={false} useNativeWindowDrag={false} />);
    const avatar = screen.getByTitle("Nyx 头像");
    Object.defineProperty(avatar, "getBoundingClientRect", {
      value: () => ({ left: 0, top: 0, right: 144, bottom: 144, width: 144, height: 144 }),
    });
    Object.defineProperty(avatar, "setPointerCapture", { value: vi.fn() });
    Object.defineProperty(avatar, "hasPointerCapture", { value: vi.fn(() => true) });
    Object.defineProperty(avatar, "releasePointerCapture", { value: vi.fn() });
    const dispatchPointer = (type: string, clientX: number, clientY: number) => {
      const event = new Event(type, { bubbles: true });
      Object.defineProperties(event, {
        pointerId: { value: 1 },
        clientX: { value: clientX },
        clientY: { value: clientY },
      });
      fireEvent(avatar, event);
    };
    const drag = () => {
      dispatchPointer("pointerdown", 10, 10);
      dispatchPointer("pointermove", 20, 10);
      dispatchPointer("pointerup", 20, 10);
    };

    drag();
    expect(desktopWindowMocks.startNativeWindowDrag).not.toHaveBeenCalled();

    rerender(<Avatar night={false} useNativeWindowDrag />);
    drag();
    expect(desktopWindowMocks.startNativeWindowDrag).toHaveBeenCalledTimes(1);
  });

  it("夜间由 App 的统一时钟切为困倦表情", () => {
    useInnerLifeStore.setState({
      current: {
        emotion: "happy",
        valence: 0.1,
        arousal: 0.2,
        personality: {
          openness: 8, conscientiousness: 8, extraversion: 2,
          agreeableness: 6, neuroticism: 7,
        },
        values: {
          attitude_to_human: 8, ai_identity_acceptance: 6, altruism: 9, optimism: 5,
        },
        aesthetic: { ornate: 7, lyrical: 7, classical: 6, somber: 6 },
        energy: 80,
        energy_state: "energetic",
        current_activity: null,
        active_desires: [],
      },
      error: null,
    });
    const { rerender } = render(<Avatar night={false} />);
    expect(screen.getByRole("img")).toHaveAttribute("alt", "happy");

    rerender(<Avatar night />);
    expect(screen.getByRole("img")).toHaveAttribute("alt", "sleepy");
  });
});
