import { useEffect, useRef, useState } from "react";
import { ActivityIndicator, AppState, BackHandler, Button, Linking, SafeAreaView, Text } from "react-native";
import Constants from "expo-constants";
import * as SecureStore from "expo-secure-store";
import WebView, { type WebViewMessageEvent } from "react-native-webview";
import policy from "../../nyx/settings/frontend.json";

const copy = policy.mobileAndroid;
const origin = Constants.expoConfig?.extra?.nyxOrigin as string;
const credentialKey = "nyx-device-token";

export default function App() {
  const web = useRef<WebView>(null);
  const canGoBack = useRef(false);
  const credential = useRef("");
  const credentialWrites = useRef(Promise.resolve());
  const pageReady = useRef(false);
  const mobileDocument = useRef(false);
  const [loaded, setLoaded] = useState(false);
  const [failed, setFailed] = useState(false);
  const [loadAttempt, setLoadAttempt] = useState(0);

  function inject(name: string, payload: unknown) {
    if (!pageReady.current) return;
    web.current?.injectJavaScript(
      `if (location.origin === ${JSON.stringify(origin)} &&
        (location.pathname === "/mobile" || location.pathname === "/mobile/")) {
        window.dispatchEvent(new CustomEvent(${JSON.stringify(name)}, { detail: ${JSON.stringify(payload)} }));
      } true;`,
    );
  }

  useEffect(() => {
    let mounted = true;
    void credentialWrites.current.catch(() => {}).then(
      () => SecureStore.getItemAsync(credentialKey),
    ).then((value) => {
      if (mounted) credential.current = value ?? "";
    }).catch(() => {
      if (mounted) setFailed(true);
    }).finally(() => {
      if (mounted) setLoaded(true);
    });
    const back = BackHandler.addEventListener("hardwareBackPress", () => {
      if (!pageReady.current) return false;
      inject("nyx:back", canGoBack.current);
      return true;
    });
    const state = AppState.addEventListener("change", (next) => {
      if (next === "active") inject("nyx:active", null);
    });
    return () => { mounted = false; back.remove(); state.remove(); };
  }, [loadAttempt]);

  function allowed(url: string) {
    try {
      const destination = new URL(url);
      return destination.origin === origin && destination.pathname.replace(/\/$/, "") === "/mobile";
    } catch { return false; }
  }

  async function message(event: WebViewMessageEvent) {
    const source = event.nativeEvent.url;
    // Modern Android WebView reports the sender's origin without a path.
    if (!mobileDocument.current || (source !== origin && !allowed(source))) return;
    let data: { type?: unknown; token?: unknown };
    try { data = JSON.parse(event.nativeEvent.data); } catch { return; }
    if (data === null || typeof data !== "object") return;
    if (data.type === "ready") {
      pageReady.current = true;
      inject("nyx:credential", credential.current);
      return;
    }
    let token: string;
    if (data.type === "paired" && typeof data.token === "string" && /^[A-Za-z0-9_-]+$/.test(data.token)) {
      token = data.token;
    } else if (data.type === "logout") {
      token = "";
    } else {
      return;
    }
    credential.current = token;
    const write = credentialWrites.current.catch(() => {}).then(() => token
      ? SecureStore.setItemAsync(credentialKey, token)
      : SecureStore.deleteItemAsync(credentialKey));
    credentialWrites.current = write;
    await write;
  }

  if (!loaded || failed) {
    return <SafeAreaView style={{ flex: 1, alignItems: "center", justifyContent: "center" }}>
      <Text>{failed ? copy.load_error : copy.loading}</Text>
      {failed ? <Button title={copy.retry} onPress={() => {
        setLoaded(false);
        setFailed(false);
        setLoadAttempt((attempt) => attempt + 1);
      }} /> : <ActivityIndicator />}
    </SafeAreaView>;
  }
  return <SafeAreaView style={{ flex: 1 }}>
    <WebView
      ref={web}
      source={{ uri: origin + "/mobile" }}
      originWhitelist={["*"]}
      mixedContentMode="never"
      allowFileAccess={false}
      setSupportMultipleWindows={false}
      onShouldStartLoadWithRequest={(request) => {
        if (allowed(request.url)) return true;
        if (request.isTopFrame !== false && /^https:\/\//i.test(request.url)) void Linking.openURL(request.url).catch(() => {});
        return false;
      }}
      onLoadStart={({ nativeEvent }) => {
        mobileDocument.current = allowed(nativeEvent.url);
        pageReady.current = false;
      }}
      onNavigationStateChange={(state) => { canGoBack.current = state.canGoBack; }}
      onError={() => setFailed(true)}
      onMessage={(event) => void message(event).catch(() => setFailed(true))}
    />
  </SafeAreaView>;
}
