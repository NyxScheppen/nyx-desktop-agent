import { useEffect, useRef, useState } from "react";
import { isTauri } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";
import { LogicalSize } from "@tauri-apps/api/dpi";
import { getCurrentWindow } from "@tauri-apps/api/window";
import { deleteBrowsingHistory, deleteBrowsingPage, getBrowsingSession, retryBrowsingPage } from "../../api/client";
import { useBrowserStore, type BrowserHostEvent, type BrowserHostResult } from "../../stores/browserStore";
import type { BrowsingPage } from "../../types/api";

export default function BrowserView({ active }: { active: boolean }) {
  const browser = useBrowserStore();
  const body = useRef<HTMLDivElement>(null);
  const [address, setAddress] = useState("");
  const [historyOpen, setHistoryOpen] = useState(false);
  const [pages, setPages] = useState<BrowsingPage[]>([]);
  const [historyError, setHistoryError] = useState<string | null>(null);
  const native = isTauri();
  const enabled = native && browser.sessionId !== null && !browser.closed;
  const bounds = () => {
    const rect = body.current!.getBoundingClientRect();
    return { x: rect.x, y: rect.y, width: rect.width, height: rect.height };
  };

  useEffect(() => { setAddress(browser.currentUrl); }, [browser.currentUrl]);
  useEffect(() => {
    if (!native || !active) return;
    const window = getCurrentWindow();
    void Promise.all([window.innerSize(), window.scaleFactor()]).then(([size, scale]) => {
      if (size.width / scale < 960) return window.setSize(new LogicalSize(960, size.height / scale));
    }).catch(() => useBrowserStore.setState({ error: "浏览窗口展开失败" }));
  }, [active, native]);
  useEffect(() => {
    if (!native) return;
    const subscriptions = [
      listen<BrowserHostEvent>("browser_host", ({ payload }) => useBrowserStore.getState().acceptEvent(payload)),
      listen<BrowserHostResult>("browser_capture", ({ payload }) => {
        const current = useBrowserStore.getState();
        if (!current.closed && payload.navigation_id === current.navigationId) current.acceptResult(payload);
      }),
    ];
    return () => {
      void Promise.all(subscriptions).then((unlisten) => unlisten.forEach((stop) => stop()));
      void useBrowserStore.getState().setVisible(false).catch(() => undefined);
    };
  }, [native]);

  useEffect(() => {
    if (!native) return;
    void browser.setVisible(active && !historyOpen).catch(() => undefined);
    if (!active || historyOpen || body.current === null) return;
    const update = () => {
      const next = bounds();
      if (next.width > 0 && next.height > 0) void browser.setBounds(next);
    };
    const observer = new ResizeObserver(update);
    observer.observe(body.current); window.addEventListener("resize", update); update();
    return () => { observer.disconnect(); window.removeEventListener("resize", update); };
  }, [active, historyOpen, native, browser.sessionId, browser.closed, browser.setVisible, browser.setBounds]);

  const refreshHistory = async () => {
    if (browser.sessionId === null) return;
    try { setPages((await getBrowsingSession(browser.sessionId)).pages); setHistoryError(null); }
    catch { setHistoryError("浏览记录读取失败"); }
  };
  useEffect(() => {
    if (!active || !historyOpen) return;
    void refreshHistory();
    const timer = setInterval(() => void refreshHistory(), 5000);
    return () => clearInterval(timer);
  }, [active, historyOpen, browser.sessionId]);

  const toggleHistory = async () => {
    try { await browser.setVisible(historyOpen); setHistoryOpen(!historyOpen); }
    catch { /* Keep native content out of the history panel. */ }
  };
  const historyAction = async (action: () => Promise<unknown>) => {
    try { await action(); await refreshHistory(); }
    catch { setHistoryError("当前记录不可操作"); }
  };

  return (
    <div className="browser-view" hidden={!active}>
      <form className="browser-toolbar" onSubmit={(event) => {
        event.preventDefault();
        if (!native || historyOpen || browser.busy || !address.trim() || body.current === null) return;
        void (enabled ? browser.navigate(address) : browser.open(address, bounds()));
      }}>
        <button type="button" title="后退" aria-label="后退" disabled={!enabled || !browser.canGoBack || browser.busy} onClick={() => void browser.back()}>←</button>
        <button type="button" title="前进" aria-label="前进" disabled={!enabled || !browser.canGoForward || browser.busy} onClick={() => void browser.forward()}>→</button>
        <button type="button" title={browser.loading ? "停止" : "刷新"} aria-label={browser.loading ? "停止" : "刷新"} disabled={!enabled || browser.busy} onClick={() => void (browser.loading ? browser.stop() : browser.reload())}>{browser.loading ? "×" : "↻"}</button>
        <input aria-label="网址" value={address} onChange={(event) => setAddress(event.target.value)} placeholder="https://" disabled={!native} />
        <button type="submit" aria-label="打开网页" title="打开网页" disabled={!native || historyOpen || browser.busy || !address.trim()}>↵</button>
        <button type="button" aria-label="关闭浏览" title="关闭浏览" disabled={!enabled || browser.busy} onClick={() => void browser.close()}>×</button>
      </form>
      <div className="browser-privacy">
        <label><input type="checkbox" checked={browser.authMode} disabled={!enabled || browser.busy} onChange={(event) => void browser.setAuthMode(event.target.checked)} />登录模式</label>
        <button disabled={!enabled || browser.loading || browser.busy || browser.authMode} onClick={() => void (browser.capturePaused ? browser.authorize() : browser.capture())}>让 Nyx 看</button>
        <button disabled={!enabled || browser.capturePaused || browser.busy} onClick={() => void browser.focus()}>讨论选区</button>
        {browser.capturePaused
          ? <button disabled={!enabled || browser.loading || browser.authMode || browser.busy} onClick={() => void browser.authorize()}>允许本次会话查看此站点</button>
          : <button disabled={!enabled || browser.busy} onClick={() => void browser.pause()}>暂停 Nyx 查看</button>}
        <button disabled={!enabled || browser.busy} onClick={() => void browser.allowPopup()}>允许登录弹窗</button>
        <button disabled={browser.sessionId === null} onClick={() => void toggleHistory()}>浏览记录</button>
      </div>
      <div className="browser-status" role="status">
        <span>{!native ? "共同浏览不可用" : browser.loading ? "加载中" : browser.capturePaused ? "Nyx 查看已暂停" : browser.title || "当前页面"}</span>
        {browser.integrationStatus && <span>{browser.integrationStatus}</span>}
      </div>
      {browser.error && <p className="error-text" role="alert">{browser.error}</p>}
      {historyOpen && <div className="browser-history">
        {historyError && <p className="error-text" role="alert">{historyError}</p>}
        <div className="browser-privacy">
          <button disabled={!browser.closed || browser.busy} onClick={() => {
            if (window.confirm("清除浏览 cookie 与缓存？Nyx 的浏览记忆会保留。")) void browser.clearData();
          }}>清除 cookie 与缓存</button>
          <button disabled={!browser.closed} onClick={() => {
            if (window.confirm("删除浏览 checkpoint 与 Nyx 浏览记忆？聊天、事件和评估记录会保留。")) void historyAction(deleteBrowsingHistory);
          }}>删除浏览 checkpoint 与 Nyx 浏览记忆</button>
        </div>
        {pages.length === 0 && <p>暂无浏览记录</p>}
        {pages.map((page) => <div className="browser-history__row" key={page.id}>
          <strong>{page.title || page.url}</strong><span>{page.url}</span><span>{page.status}</span>
          {page.last_error && <span>{page.last_error}</span>}
          {page.status === "failed" && <button onClick={() => void historyAction(() => retryBrowsingPage(page.id))}>重试</button>}
          <button disabled={page.id === browser.pageId || !["failed", "remembered"].includes(page.status)} onClick={() => {
            if (window.confirm("删除此页浏览 checkpoint 与记忆？")) void historyAction(() => deleteBrowsingPage(page.id));
          }}>删除</button>
        </div>)}
      </div>}
      <div className="browser-body" ref={body} aria-label="网页内容" />
    </div>
  );
}
