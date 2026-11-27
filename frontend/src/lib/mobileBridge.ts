import { useConnectionStore } from "../stores/connectionStore";

type AndroidWindow = Window & { ReactNativeWebView?: { postMessage: (payload: string) => void } };

export function postToAndroid(type: string, data?: { token: string }): void {
  (window as AndroidWindow).ReactNativeWebView?.postMessage(JSON.stringify({ type, ...data }));
}

export function connectAndroid(): (() => void) | undefined {
  if (!(window as AndroidWindow).ReactNativeWebView) return;
  function credential(event: Event) {
    const token: unknown = (event as CustomEvent).detail;
    if (typeof token === "string" && token) useConnectionStore.getState().setToken(token);
  }
  window.addEventListener("nyx:credential", credential);
  const unsubscribe = useConnectionStore.subscribe((state, old) => {
    if (old.token && (!state.token || (!old.required && state.required))) {
      postToAndroid("logout");
    }
  });
  postToAndroid("ready");
  return () => {
    unsubscribe();
    window.removeEventListener("nyx:credential", credential);
  };
}
