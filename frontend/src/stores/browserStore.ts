import { invoke } from "@tauri-apps/api/core";
import { create } from "zustand";

export type BrowserBounds = { x: number; y: number; width: number; height: number };
export type BrowserHostResult = {
  ok: boolean; session_id: string; navigation_id: string; page_id: string | null;
  revision: number | null; current_url: string; title: string; loading: boolean;
  can_go_back: boolean; can_go_forward: boolean; capture_paused: boolean;
  integration_status: string | null;
};
export type BrowserHostEvent = Omit<BrowserHostResult, "ok" | "page_id" | "revision" | "integration_status"> & {
  kind: string; error_code?: string | null;
};

type BrowserState = {
  sessionId: string | null; navigationId: string | null; pageId: string | null;
  revision: number | null; currentUrl: string; title: string; loading: boolean;
  canGoBack: boolean; canGoForward: boolean; capturePaused: boolean;
  integrationStatus: string | null; busy: boolean; visible: boolean; closed: boolean;
  authMode: boolean; error: string | null; pendingFocusId: string | null;
  acceptResult: (result: BrowserHostResult) => void; acceptEvent: (event: BrowserHostEvent) => void;
  open: (url: string, bounds: BrowserBounds) => Promise<boolean>;
  navigate: (url: string) => Promise<boolean>;
  back: () => Promise<boolean>; forward: () => Promise<boolean>;
  reload: () => Promise<boolean>; stop: () => Promise<boolean>;
  capture: () => Promise<boolean>; focus: () => Promise<boolean>;
  setAuthMode: (enabled: boolean) => Promise<boolean>;
  authorize: () => Promise<boolean>; pause: () => Promise<boolean>;
  close: () => Promise<boolean>; clearData: () => Promise<boolean>;
  allowPopup: () => Promise<boolean>;
  setVisible: (visible: boolean) => Promise<void>; setBounds: (bounds: BrowserBounds) => Promise<void>;
};

const ERROR_LABELS: Record<string, string> = {
  unsafe_url: "只能浏览公网 HTTPS 网页", invalid_url: "网址无效",
  backend_unavailable: "桌面配对或后端不可用", origin_authorization_required: "Nyx 查看已暂停，需离开敏感页面并授权",
  invalid_bridge_token: "桌面配对已失效，Nyx 查看已暂停",
  popup_unsupported: "此平台尚不支持登录弹窗", load_failed: "网页加载或读取失败",
  popup_not_allowed: "登录弹窗许可已过期或已有弹窗",
  duplicate_page_not_ready: "此页仍在整合，请稍后重新采集", browsing_storage_limit: "浏览存储已满",
  not_ready: "页面尚未就绪", stale_navigation: "页面已切换", state_conflict: "当前状态不允许此操作",
};

const POPUP_LABELS: Record<string, string | null> = {
  success: null, cancelled: "登录弹窗已关闭", timeout: "登录弹窗已超时",
  provider_refused: "此网站不支持窗口内登录", unsupported: "此登录方式不兼容",
  failed: "登录弹窗未能打开",
};

export const useBrowserStore = create<BrowserState>((set, get) => {
  const run = async (command: string, args?: Record<string, unknown>, advances = false): Promise<boolean> => {
    if (get().busy) return false;
    const before = get().navigationId;
    set({ busy: true, error: null, ...(advances ? { pageId: null, revision: null, capturePaused: true } : {}) });
    try {
      const result = await invoke<BrowserHostResult>(command, args);
      const current = get().navigationId;
      if (result.navigation_id !== current && !(advances && current === before)) return false;
      get().acceptResult(result);
      if (command === "browser_create") await invoke(get().visible ? "browser_show" : "browser_hide");
      return true;
    } catch (error) {
      const code = typeof error === "object" && error !== null && "code" in error ? String(error.code) : "internal";
      if (get().navigationId === before || advances) set({ error: ERROR_LABELS[code] ?? "浏览操作失败" });
      if (code === "invalid_bridge_token" && get().navigationId === before) {
        set({ capturePaused: true, pageId: null, revision: null, pendingFocusId: null });
      }
      if (command === "browser_focus" && code !== "backend_unavailable") set({ pendingFocusId: null });
      return false;
    } finally { set({ busy: false }); }
  };
  const origin = () => new URL(get().currentUrl).origin;
  return {
    sessionId: null, navigationId: null, pageId: null, revision: null, currentUrl: "", title: "",
    loading: false, canGoBack: false, canGoForward: false, capturePaused: true,
    integrationStatus: null, busy: false, visible: false, closed: false, authMode: false,
    error: null, pendingFocusId: null,
    acceptResult: (r) => {
      if (get().sessionId !== null && r.session_id !== get().sessionId && !get().closed) return;
      set({ sessionId: r.session_id, navigationId: r.navigation_id, pageId: r.capture_paused ? null : r.page_id,
        revision: r.revision, currentUrl: r.current_url, title: r.title, loading: r.loading,
        canGoBack: r.can_go_back, canGoForward: r.can_go_forward, capturePaused: r.capture_paused,
        integrationStatus: r.integration_status });
    },
    acceptEvent: (e) => {
      if (get().closed || (get().sessionId !== null && e.session_id !== get().sessionId)) return;
      const starts = e.kind === "navigation_started" || (e.kind === "auth_state_changed" && e.loading && e.capture_paused);
      if (!starts && e.navigation_id !== get().navigationId) return;
      set({ sessionId: e.session_id, navigationId: e.navigation_id, currentUrl: e.current_url,
        title: e.title, loading: e.loading, canGoBack: e.can_go_back, canGoForward: e.can_go_forward,
        capturePaused: e.capture_paused,
        ...(starts || e.capture_paused ? { pageId: null, revision: null, pendingFocusId: null } : {}),
        ...(e.error_code ? { error: e.kind === "popup_result"
          ? POPUP_LABELS[e.error_code] ?? "登录弹窗已结束"
          : ERROR_LABELS[e.error_code] ?? "浏览操作失败" } : {}),
      });
    },
    open: async (url, bounds) => {
      if (get().closed) set({ sessionId: null, navigationId: null, closed: false, authMode: false });
      return run("browser_create", { initialUrl: url, bounds }, true);
    },
    navigate: (url) => run("browser_navigate", { url }, true),
    back: () => run("browser_back", undefined, true), forward: () => run("browser_forward", undefined, true),
    reload: () => run("browser_reload", undefined, true), stop: () => run("browser_stop"),
    capture: () => run("browser_capture", { navigationId: get().navigationId }),
    focus: async () => {
      const id = get().pendingFocusId ?? crypto.randomUUID(); set({ pendingFocusId: id });
      const accepted = await run("browser_focus", { navigationId: get().navigationId, focusId: id });
      if (accepted) set({ pendingFocusId: null }); return accepted;
    },
    setAuthMode: async (enabled) => {
      if (enabled) set({ authMode: true, capturePaused: true, pageId: null, revision: null });
      const accepted = await run("browser_set_auth_mode", { enabled });
      if (accepted) set({ authMode: enabled }); return accepted;
    },
    authorize: () => run("browser_authorize_origin", { navigationId: get().navigationId, origin: origin() }),
    pause: () => { set({ capturePaused: true, pageId: null, revision: null }); return run("browser_revoke_origin", { origin: origin() }); },
    close: async () => {
      set({ pageId: null, revision: null, capturePaused: true, pendingFocusId: null });
      const accepted = await run("browser_close"); if (accepted) set({ closed: true, loading: false }); return accepted;
    },
    clearData: () => run("browser_clear_data"),
    allowPopup: () => run("browser_allow_auth_popup"),
    setVisible: async (visible) => {
      set({ visible }); if (get().sessionId === null || get().closed) return;
      try { await invoke(visible ? "browser_show" : "browser_hide"); }
      catch { set({ error: "浏览窗口显隐失败" }); throw new Error("browser_visibility_failed"); }
    },
    setBounds: async (bounds) => {
      if (get().sessionId === null || get().closed) return;
      try { await invoke("browser_set_bounds", { bounds }); }
      catch { set({ error: "浏览窗口尺寸更新失败" }); }
    },
  };
});
