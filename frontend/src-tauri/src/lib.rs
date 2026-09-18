use std::collections::{HashMap, HashSet};
use std::net::IpAddr;
use std::sync::Mutex;
use std::time::{Duration, Instant};

use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use tauri::{Emitter, Manager};

#[derive(Debug, Serialize)]
struct BrowserCommandError {
    code: String,
    message: &'static str,
    retryable: bool,
}

fn browser_error(code: &str) -> BrowserCommandError {
    BrowserCommandError {
        code: code.into(),
        message: "Browser operation was rejected",
        retryable: code == "backend_unavailable",
    }
}

#[derive(Clone, Default, Serialize)]
struct BrowserCommandResult {
    ok: bool,
    session_id: String,
    navigation_id: String,
    page_id: Option<String>,
    revision: Option<u64>,
    current_url: String,
    title: String,
    loading: bool,
    can_go_back: bool,
    can_go_forward: bool,
    capture_paused: bool,
    integration_status: Option<String>,
}

#[derive(Clone, Copy, Deserialize)]
struct BrowserBounds {
    x: f64,
    y: f64,
    width: f64,
    height: f64,
}

impl BrowserBounds {
    fn validate(self) -> Result<Self, BrowserCommandError> {
        if [self.x, self.y, self.width, self.height]
            .iter()
            .any(|v| !v.is_finite())
            || self.x < 0.0
            || self.y < 0.0
            || self.width <= 0.0
            || self.height <= 0.0
        {
            return Err(browser_error("invalid_payload"));
        }
        Ok(self)
    }
}

#[derive(Default)]
struct BrowserState {
    session: String,
    token: String,
    navigation: String,
    url: String,
    title: String,
    page: Option<String>,
    revision: Option<u64>,
    status: Option<String>,
    sequence: u64,
    loading: bool,
    paused: bool,
    auth_mode: bool,
    popup_permit: Option<Instant>,
    popup_id: Option<String>,
    closed: bool,
    back: bool,
    forward: bool,
    granted: HashSet<String>,
    tainted: HashSet<String>,
    permitted_url: Option<String>,
    expected_load: bool,
    verified_at: Option<Instant>,
    synced_navigation: Option<String>,
    engine_navigations: HashMap<u64, String>,
    // Preserve the original selected text if the HTTP response was lost.
    focus_requests: HashMap<String, Value>,
}

impl BrowserState {
    fn take_popup_permit(&mut self, now: Instant) -> Result<String, BrowserCommandError> {
        if self.closed
            || self.popup_id.is_some()
            || !self.popup_permit.take().is_some_and(|until| now <= until)
        {
            return Err(browser_error("popup_not_allowed"));
        }
        let id = uuid::Uuid::new_v4().to_string();
        self.popup_id = Some(id.clone());
        Ok(id)
    }

    fn result(&self) -> BrowserCommandResult {
        BrowserCommandResult {
            ok: true,
            session_id: self.session.clone(),
            navigation_id: self.navigation.clone(),
            page_id: self.page.clone(),
            revision: self.revision,
            current_url: sanitize_url(&self.url),
            title: self.title.clone(),
            loading: self.loading,
            can_go_back: self.back,
            can_go_forward: self.forward,
            capture_paused: self.paused,
            integration_status: self.status.clone(),
        }
    }

    fn start_navigation(&mut self, url: &tauri::Url) -> String {
        self.navigation = uuid::Uuid::new_v4().to_string();
        self.url = url.to_string();
        self.title.clear();
        self.page = None;
        self.revision = None;
        self.status = None;
        self.sequence = 0;
        self.synced_navigation = None;
        self.loading = true;
        self.paused = true;
        self.focus_requests.clear();
        self.navigation.clone()
    }
}

struct BrowserHost {
    secret: Option<String>,
    client: reqwest::Client,
    state: Mutex<BrowserState>,
    gate: tokio::sync::Mutex<()>,
}

fn public_ip(ip: IpAddr) -> bool {
    match ip {
        IpAddr::V4(ip) => {
            let n = u32::from(ip);
            ![
                (0x00000000, 8),
                (0x0a000000, 8),
                (0x64400000, 10),
                (0x7f000000, 8),
                (0xa9fe0000, 16),
                (0xac100000, 12),
                (0xc0000000, 24),
                (0xc0000200, 24),
                (0xc0586300, 24),
                (0xc0a80000, 16),
                (0xc6120000, 15),
                (0xc6336400, 24),
                (0xcb007100, 24),
                (0xe0000000, 4),
                (0xf0000000, 4),
            ]
            .iter()
            .any(|(network, bits)| n >> (32 - bits) == network >> (32 - bits))
        }
        IpAddr::V6(ip) => {
            if let Some(ip) = ip.to_ipv4_mapped() {
                return public_ip(IpAddr::V4(ip));
            }
            let s = ip.segments();
            // Only ordinary global unicast; reject protocol assignments, documentation and 6to4.
            s[0] & 0xe000 == 0x2000
                && !(s[0] == 0x2001 && (s[1] < 0x200 || s[1] == 0xdb8))
                && s[0] != 0x2002
                && !(s[0] == 0x3fff && s[1] < 0x1000)
        }
    }
}

fn browser_url(input: &str) -> Result<tauri::Url, BrowserCommandError> {
    let input = input.trim();
    let value = if input.contains("://") {
        input.to_owned()
    } else {
        format!("https://{input}")
    };
    let url = tauri::Url::parse(&value).map_err(|_| browser_error("invalid_url"))?;
    if url.scheme() != "https"
        || url.host_str().is_none()
        || !url.username().is_empty()
        || url.password().is_some()
        || url.as_str().chars().count() > 8192
    {
        return Err(browser_error("unsafe_url"));
    }
    let host = url
        .host_str()
        .unwrap()
        .trim_matches(['[', ']'])
        .to_ascii_lowercase();
    if host.trim_end_matches('.') == "localhost"
        || host.ends_with(".localhost")
        || host.parse::<IpAddr>().is_ok_and(|ip| !public_ip(ip))
    {
        return Err(browser_error("unsafe_url"));
    }
    Ok(url)
}

async fn preflight(input: &str) -> Result<tauri::Url, BrowserCommandError> {
    #[cfg(all(test, target_os = "windows"))]
    if let Ok(origin) = std::env::var("NYX_OAUTH_FIXTURE_ORIGIN") {
        if let Ok(url) = tauri::Url::parse(input) {
            if url.scheme() == "https"
                && url.host_str() == Some("127.0.0.1")
                && url.origin().ascii_serialization() == origin
            {
                let _addresses = tokio::time::timeout(
                    Duration::from_secs(2),
                    tokio::net::lookup_host(("127.0.0.1", url.port().unwrap())),
                )
                .await
                .map_err(|_| browser_error("load_failed"))?
                .map_err(|_| browser_error("load_failed"))?;
                return Ok(url);
            }
        }
    }
    let url = browser_url(input)?;
    let host = url.host_str().unwrap().trim_matches(['[', ']']);
    let addresses = tokio::time::timeout(
        Duration::from_secs(5),
        tokio::net::lookup_host((host, url.port_or_known_default().unwrap_or(443))),
    )
    .await
    .map_err(|_| browser_error("load_failed"))?
    .map_err(|_| browser_error("load_failed"))?;
    let addresses: Vec<_> = addresses.collect();
    if addresses.is_empty() || addresses.iter().any(|address| !public_ip(address.ip())) {
        return Err(browser_error("unsafe_url"));
    }
    Ok(url)
}

fn decode_signal(value: &str) -> Option<String> {
    let bytes = value.as_bytes();
    for (index, byte) in bytes.iter().enumerate() {
        if *byte == b'%'
            && (index + 2 >= bytes.len()
                || !bytes[index + 1].is_ascii_hexdigit()
                || !bytes[index + 2].is_ascii_hexdigit())
        {
            return None;
        }
    }
    // Only casefold expansions capable of matching the fixed ASCII signals are relevant.
    Some(
        percent_encoding::percent_decode_str(value)
            .decode_utf8()
            .ok()?
            .to_lowercase()
            .replace('ſ', "s")
            .replace('ß', "ss")
            .replace(['ﬅ', 'ﬆ'], "st"),
    )
}

fn sensitive_url(url: &tauri::Url) -> bool {
    let Some(path) = decode_signal(url.path()) else {
        return true;
    };
    if path.split('/').any(|part| {
        matches!(
            part,
            "auth"
                | "login"
                | "log-in"
                | "signin"
                | "sign-in"
                | "signup"
                | "sign-up"
                | "register"
                | "oauth"
                | "authorize"
                | "sso"
                | "callback"
                | "password"
                | "reset-password"
                | "mfa"
                | "2fa"
                | "checkout"
                | "payment"
                | "billing"
                | "bank"
                | "banking"
                | "inbox"
                | "mail"
                | "medical"
                | "health"
                | "patient"
        )
    }) {
        return true;
    }
    for query in [url.query(), url.fragment()].into_iter().flatten() {
        for pair in query.split('&') {
            let key = pair.split('=').next().unwrap_or("").replace('+', " ");
            let Some(key) = decode_signal(&key) else {
                return true;
            };
            if matches!(
                key.as_str(),
                "code"
                    | "state"
                    | "access_token"
                    | "id_token"
                    | "refresh_token"
                    | "oauth_token"
                    | "samlrequest"
                    | "samlresponse"
                    | "client_id"
                    | "redirect_uri"
                    | "response_type"
                    | "code_challenge"
            ) {
                return true;
            }
        }
    }
    false
}

fn sanitize_url(value: &str) -> String {
    let Ok(mut url) = tauri::Url::parse(value) else {
        return String::new();
    };
    let _ = url.set_username("");
    let _ = url.set_password(None);
    url.set_query(None);
    url.set_fragment(None);
    url.to_string()
}

fn check_caller(webview: &tauri::Webview) -> Result<(), BrowserCommandError> {
    let url = webview
        .url()
        .map_err(|_| browser_error("capability_denied"))?;
    let origin = url.origin().ascii_serialization();
    if webview.label() != "main"
        || !matches!(
            origin.as_str(),
            "http://localhost:5173" | "http://tauri.localhost" | "tauri://localhost"
        )
    {
        return Err(browser_error("capability_denied"));
    }
    Ok(())
}

fn child(app: &tauri::AppHandle) -> Result<tauri::Webview, BrowserCommandError> {
    app.get_webview("browsing")
        .ok_or_else(|| browser_error("not_ready"))
}

fn host_event(app: &tauri::AppHandle, kind: &str, error: Option<&str>) {
    let state = app.state::<BrowserHost>();
    let state = state.state.lock().unwrap();
    let result = state.result();
    // No page text, raw URL, secret or bearer token enters events.
    let _ = app.emit_to(
        "main",
        "browser_host",
        json!({
            "kind": kind, "session_id": result.session_id, "navigation_id": result.navigation_id,
            "current_url": result.current_url, "title": result.title, "loading": result.loading,
            "can_go_back": result.can_go_back, "can_go_forward": result.can_go_forward,
            "capture_paused": result.capture_paused, "error_code": error,
        }),
    );
}

async fn bridge(
    host: &BrowserHost,
    token: &str,
    path: &str,
    body: Value,
) -> Result<Value, BrowserCommandError> {
    let base = "http://127.0.0.1:8000".to_owned();
    #[cfg(all(test, target_os = "windows"))]
    let base = std::env::var("NYX_OAUTH_FIXTURE_BRIDGE_PORT")
        .ok()
        .and_then(|port| port.parse::<u16>().ok())
        .map(|port| format!("http://127.0.0.1:{port}"))
        .unwrap_or(base);
    let response = host
        .client
        .post(format!("{base}/api/browsing/bridge/{path}"))
        .bearer_auth(token)
        .json(&body)
        .send()
        .await
        .map_err(|_| browser_error("backend_unavailable"))?;
    let success = response.status().is_success();
    let response_status = response.status().as_u16();
    let value: Value = response
        .json()
        .await
        .map_err(|_| browser_error("internal"))?;
    if success {
        return Ok(value);
    }
    let code = value["detail"]["code"].as_str().unwrap_or_else(|| {
        if response_status == 422 {
            "invalid_payload"
        } else {
            "internal"
        }
    });
    if code == "invalid_bridge_token" {
        let mut state = host.state.lock().unwrap();
        if state.token == token {
            state.paused = true;
            state.page = None;
            state.revision = None;
        }
    }
    let known = matches!(
        code,
        "invalid_url"
            | "unsafe_url"
            | "stale_navigation"
            | "not_ready"
            | "origin_authorization_required"
            | "invalid_bridge_token"
            | "session_mismatch"
            | "stale_capture"
            | "duplicate_page_not_ready"
            | "state_conflict"
            | "snapshot_expired"
            | "browsing_storage_limit"
            | "backend_unavailable"
            | "invalid_host"
            | "origin_forbidden"
            | "unsupported_media_type"
            | "request_too_large"
            | "invalid_payload"
            | "not_found"
    );
    Err(browser_error(if known { code } else { "internal" }))
}

async fn eval_json(webview: &tauri::Webview, script: &str) -> Result<Value, BrowserCommandError> {
    let (tx, rx) = tokio::sync::oneshot::channel();
    let tx = Mutex::new(Some(tx));
    webview
        .eval_with_callback(script, move |value| {
            if let Some(tx) = tx.lock().unwrap().take() {
                let _ = tx.send(value);
            }
        })
        .map_err(|_| browser_error("load_failed"))?;
    let value = tokio::time::timeout(Duration::from_secs(5), rx)
        .await
        .map_err(|_| browser_error("load_failed"))?
        .map_err(|_| browser_error("load_failed"))?;
    let value: Value = serde_json::from_str(&value).map_err(|_| browser_error("load_failed"))?;
    if value["ok"] != true {
        return Err(browser_error("load_failed"));
    }
    Ok(value)
}

const DOM_PROBE: &str = r#"(() => { try {
  const visible = e => e.getClientRects().length > 0 && getComputedStyle(e).visibility !== 'hidden';
  return {ok:true, url:location.href, password:Array.from(document.querySelectorAll('input'))
    .some(e => (e.getAttribute('type') || '').toLowerCase() === 'password'),
    frames:Array.from(document.querySelectorAll('iframe')).filter(visible).map(e => e.src)};
} catch { return {ok:false}; } })()"#;

const DOM_CAPTURE: &str = r#"(expected => { try {
  if (location.href !== expected.url) return {ok:true, changed:true};
  if (Array.from(document.querySelectorAll('input')).some(e => (e.getAttribute('type') || '').toLowerCase() === 'password'))
    return {ok:true, sensitive:true};
  const frames = Array.from(document.querySelectorAll('iframe')).filter(e => e.getClientRects().length > 0 && getComputedStyle(e).visibility !== 'hidden').map(e => e.src);
  if (JSON.stringify(frames) !== JSON.stringify(expected.frames)) return {ok:true, sensitive:true};
  const excluded = 'script,style,noscript,svg,form,input,textarea,select,button,iframe,[contenteditable]:not([contenteditable="false"]),[hidden],[aria-hidden="true"]';
  const safe = n => {
    let e = n.nodeType === Node.ELEMENT_NODE ? n : n.parentElement;
    for (; e; e = e.parentElement) {
      if (e.matches(excluded)) return false;
      const s = getComputedStyle(e);
      if (s.display === 'none' || s.visibility === 'hidden' || s.visibility === 'collapse' || Number(s.opacity) === 0) return false;
    }
    return n.parentElement?.getClientRects().length > 0;
  };
  const normalize = s => s.replace(/\s+/gu, ' ').trim();
  const clip = (s, n) => Array.from(normalize(s)).slice(0, n).join('');
  const text = root => {
    if (!root) return '';
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    const parts = []; let count = 0;
    while (walker.nextNode()) {
      if (!safe(walker.currentNode)) continue;
      const value = normalize(walker.currentNode.textContent || '');
      if (value) { parts.push(value); count += Array.from(value).length + 1; }
      if (count > 200000) break;
    }
    return normalize(parts.join(' '));
  };
  let body = text(document.querySelector('article,main,[role="main"]'));
  if (!body) body = text(document.body);
  let selected = null;
  const selection = window.getSelection();
  if (selection?.rangeCount === 1 && !selection.isCollapsed) {
    const range = selection.getRangeAt(0);
    let allowed = range.startContainer.ownerDocument === document && range.endContainer.ownerDocument === document
      && safe(range.startContainer) && safe(range.endContainer);
    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_ELEMENT | NodeFilter.SHOW_TEXT);
    while (allowed && walker.nextNode()) {
      if (range.intersectsNode(walker.currentNode) && !safe(walker.currentNode)) allowed = false;
    }
    if (allowed) selected = clip(selection.toString(), 4000) || null;
  }
  return {ok:true, url:location.href, title:clip(document.title,512), text:clip(body,200000),
    selected, canonical:document.querySelector('link[rel="canonical"]')?.getAttribute('href') || null,
    truncated:Array.from(body).length > 200000};
} catch { return {ok:false}; } })"#;

async fn revoke(
    app: &tauri::AppHandle,
    origin: &str,
    navigation: Option<&str>,
) -> Result<(), BrowserCommandError> {
    let host = app.state::<BrowserHost>();
    let (session, token) = {
        let mut state = host.state.lock().unwrap();
        if navigation.is_some_and(|navigation| state.navigation != navigation) {
            return Err(browser_error("stale_navigation"));
        }
        state.granted.remove(origin);
        state.tainted.insert(origin.into());
        state.paused = true;
        state.page = None;
        state.revision = None;
        state.focus_requests.clear();
        (state.session.clone(), state.token.clone())
    };
    host_event(app, "auth_state_changed", None);
    bridge(
        &host,
        &token,
        &format!("sessions/{session}/origins/revoke"),
        json!({"origin":origin}),
    )
    .await?;
    Ok(())
}

async fn navigate(
    app: &tauri::AppHandle,
    input: &str,
) -> Result<BrowserCommandResult, BrowserCommandError> {
    let url = preflight(input).await?;
    let host = app.state::<BrowserHost>();
    let (session, token, navigation) = {
        let mut state = host.state.lock().unwrap();
        if state.closed {
            return Err(browser_error("not_ready"));
        }
        let navigation = state.start_navigation(&url);
        (state.session.clone(), state.token.clone(), navigation)
    };
    if sensitive_url(&url) {
        revoke(app, &url.origin().ascii_serialization(), Some(&navigation)).await?;
    }
    bridge(
        &host,
        &token,
        &format!("sessions/{session}/navigations"),
        json!({"navigation_id":navigation}),
    )
    .await?;
    {
        let mut state = host.state.lock().unwrap();
        if state.navigation != navigation {
            return Err(browser_error("stale_navigation"));
        }
        state.permitted_url = Some(url.to_string());
        state.expected_load = true;
        state.verified_at = Some(Instant::now());
        state.synced_navigation = Some(navigation.clone());
    }
    host_event(app, "navigation_started", None);
    child(app)?
        .navigate(url)
        .map_err(|_| browser_error("load_failed"))?;
    let result = host.state.lock().unwrap().result();
    Ok(result)
}

async fn capture(
    app: &tauri::AppHandle,
    navigation: &str,
    metadata_only: bool,
) -> Result<BrowserCommandResult, BrowserCommandError> {
    let host = app.state::<BrowserHost>();
    {
        let state = host.state.lock().unwrap();
        if state.navigation != navigation {
            return Err(browser_error("stale_navigation"));
        }
        if state.loading
            || state.auth_mode
            || state.popup_id.is_some()
            || state.closed
            || state.synced_navigation.as_deref() != Some(navigation)
        {
            return Err(browser_error("not_ready"));
        }
    }
    let webview = child(app)?;
    let probe = match eval_json(&webview, DOM_PROBE).await {
        Ok(probe) => probe,
        Err(error) => {
            let origin = webview
                .url()
                .map_err(|_| browser_error("load_failed"))?
                .origin()
                .ascii_serialization();
            if host.state.lock().unwrap().navigation == navigation {
                revoke(app, &origin, Some(navigation)).await?;
            }
            return Err(error);
        }
    };
    let url = browser_url(
        probe["url"]
            .as_str()
            .ok_or_else(|| browser_error("load_failed"))?,
    )?;
    let origin = url.origin().ascii_serialization();
    if host.state.lock().unwrap().navigation != navigation {
        return Err(browser_error("stale_navigation"));
    }
    let bad_frame = probe["frames"].as_array().is_none_or(|frames| {
        frames.iter().any(|frame| {
            frame
                .as_str()
                .and_then(|value| tauri::Url::parse(value).ok())
                .is_none_or(|url| sensitive_url(&url))
        })
    });
    if probe["password"].as_bool().is_none_or(|password| password)
        || sensitive_url(&url)
        || bad_frame
    {
        revoke(app, &origin, Some(navigation)).await?;
        return Err(browser_error("origin_authorization_required"));
    }
    let (session, token, tainted, granted) = {
        let state = host.state.lock().unwrap();
        (
            state.session.clone(),
            state.token.clone(),
            state.tainted.contains(&origin),
            state.granted.contains(&origin),
        )
    };
    if tainted && !granted {
        bridge(&host, &token, &format!("sessions/{session}/pages"), json!({
            "session_id":session, "navigation_id":navigation, "sanitized_origin":origin, "auth_tainted":true,
        })).await?;
        return Err(browser_error("origin_authorization_required"));
    }
    let script = format!(
        "{DOM_CAPTURE}({})",
        json!({"url":url.as_str(),"frames":probe["frames"]})
    );
    let dom = match eval_json(&webview, &script).await {
        Ok(dom) => dom,
        Err(error) => {
            if host.state.lock().unwrap().navigation == navigation {
                revoke(app, &origin, Some(navigation)).await?;
            }
            return Err(error);
        }
    };
    if host.state.lock().unwrap().navigation != navigation {
        return Err(browser_error("stale_navigation"));
    }
    if dom["sensitive"] == true {
        revoke(app, &origin, Some(navigation)).await?;
        return Err(browser_error("origin_authorization_required"));
    }
    if dom["url"] != probe["url"] {
        return Err(browser_error("stale_navigation"));
    }
    let text = dom["text"]
        .as_str()
        .ok_or_else(|| browser_error("load_failed"))?;
    if text.is_empty() && !metadata_only {
        return Err(browser_error("not_ready"));
    }
    if dom["canonical"]
        .as_str()
        .is_some_and(|value| value.chars().count() > 8192)
    {
        return Err(browser_error("invalid_payload"));
    }
    let sequence = {
        let mut state = host.state.lock().unwrap();
        if state.navigation != navigation
            || state.loading
            || state.auth_mode
            || state.popup_id.is_some()
            || (state.tainted.contains(&origin) && !state.granted.contains(&origin))
        {
            return Err(browser_error("stale_navigation"));
        }
        state.sequence += 1;
        state.sequence
    };
    let response = bridge(&host, &token, &format!("sessions/{session}/pages"), json!({
        "session_id":session, "navigation_id":navigation, "capture_seq":sequence, "raw_url":url.as_str(),
        "canonical_candidate":dom["canonical"], "title":dom["title"], "visible_text":text,
        "selected_text":dom["selected"], "auth_tainted":tainted, "truncated":dom["truncated"],
    })).await?;
    let mut state = host.state.lock().unwrap();
    if state.navigation != navigation
        || state.popup_id.is_some()
        || (state.tainted.contains(&origin) && !state.granted.contains(&origin))
    {
        return Err(browser_error("stale_navigation"));
    }
    state.page = Some(
        response["page_id"]
            .as_str()
            .ok_or_else(|| browser_error("internal"))?
            .into(),
    );
    state.revision = response["revision"].as_u64();
    state.status = response["status"].as_str().map(str::to_owned);
    state.paused = false;
    Ok(state.result())
}

fn schedule_capture(app: tauri::AppHandle, navigation: String) {
    tauri::async_runtime::spawn(async move {
        for (delay, metadata_only) in [(800, false), (1200, false), (3000, true)] {
            tokio::time::sleep(Duration::from_millis(delay)).await;
            let host = app.state::<BrowserHost>();
            let _gate = host.gate.lock().await;
            match capture(&app, &navigation, metadata_only).await {
                Ok(result) => {
                    let _ = app.emit_to("main", "browser_capture", result);
                    break;
                }
                Err(error) if error.code == "not_ready" => continue,
                Err(error) => {
                    host_event(&app, "auth_state_changed", Some(&error.code));
                    break;
                }
            }
        }
    });
}

fn engine_load_start(app: &tauri::AppHandle, url: &tauri::Url) {
    if url.scheme() == "about" {
        return;
    }
    let host = app.state::<BrowserHost>();
    let (navigation, expected) = {
        let mut state = host.state.lock().unwrap();
        let expected = state.expected_load;
        state.expected_load = false;
        let navigation = if expected {
            state.url = url.to_string();
            state.navigation.clone()
        } else {
            state.start_navigation(url)
        };
        if sensitive_url(url) {
            let origin = url.origin().ascii_serialization();
            state.granted.remove(&origin);
            state.tainted.insert(origin);
        }
        (navigation, expected)
    };
    if expected {
        host_event(app, "navigation_committed", None);
        return;
    }
    host_event(app, "auth_state_changed", None);
    let app = app.clone();
    let url = url.clone();
    tauri::async_runtime::spawn(async move {
        let host = app.state::<BrowserHost>();
        let _gate = host.gate.lock().await;
        if host.state.lock().unwrap().navigation != navigation {
            return;
        }
        let result = async {
            if sensitive_url(&url) {
                revoke(&app, &url.origin().ascii_serialization(), Some(&navigation)).await?;
            }
            let (session, token) = {
                let state = host.state.lock().unwrap();
                (state.session.clone(), state.token.clone())
            };
            bridge(
                &host,
                &token,
                &format!("sessions/{session}/navigations"),
                json!({"navigation_id":navigation}),
            )
            .await?;
            {
                let mut state = host.state.lock().unwrap();
                if state.navigation == navigation {
                    state.synced_navigation = Some(navigation.clone());
                }
            }
            Ok::<(), BrowserCommandError>(())
        }
        .await;
        if host.state.lock().unwrap().navigation == navigation {
            host_event(
                &app,
                "navigation_started",
                result.err().as_ref().map(|error| error.code.as_str()),
            );
        }
    });
}

#[cfg(target_os = "windows")]
async fn install_native_guards(
    app: &tauri::AppHandle,
    webview: &tauri::Webview,
) -> Result<(), BrowserCommandError> {
    use webview2_com::Microsoft::Web::WebView2::Win32::{
        ICoreWebView2Settings4, COREWEBVIEW2_PERMISSION_STATE_DENY,
    };
    use webview2_com::{
        HistoryChangedEventHandler, NavigationCompletedEventHandler,
        NavigationStartingEventHandler, PermissionRequestedEventHandler, ProcessFailedEventHandler,
        WindowCloseRequestedEventHandler,
    };
    use windows::core::{Interface, BOOL};

    let app = app.clone();
    let popup = webview.label() == "browsing-auth";
    let (tx, rx) = tokio::sync::oneshot::channel();
    webview
        .with_webview(move |platform| {
            let result = (|| -> windows::core::Result<()> {
                unsafe {
                    let core = platform.controller().CoreWebView2()?;
                    let settings = core.Settings()?;
                    settings.SetAreDevToolsEnabled(false)?;
                    settings.SetAreDefaultContextMenusEnabled(false)?;
                    settings.SetAreHostObjectsAllowed(false)?;
                    settings.SetIsStatusBarEnabled(false)?;
                    settings.SetIsBuiltInErrorPageEnabled(false)?;
                    let settings: ICoreWebView2Settings4 = settings.cast()?;
                    settings.SetIsPasswordAutosaveEnabled(false)?;
                    settings.SetIsGeneralAutofillEnabled(false)?;
                    let mut token = 0;
                    core.add_PermissionRequested(
                        &PermissionRequestedEventHandler::create(Box::new(|_, args| {
                            if let Some(args) = args {
                                args.SetState(COREWEBVIEW2_PERMISSION_STATE_DENY)?;
                            }
                            Ok(())
                        })),
                        &mut token,
                    )?;
                    if popup {
                        let close_app = app.clone();
                        core.add_WindowCloseRequested(
                            &WindowCloseRequestedEventHandler::create(Box::new(move |_, _| {
                                let id = close_app
                                    .state::<BrowserHost>()
                                    .state
                                    .lock()
                                    .unwrap()
                                    .popup_id
                                    .clone();
                                if let Some(id) = id {
                                    tauri::async_runtime::spawn(finish_auth_popup(
                                        close_app.clone(),
                                        id,
                                        "cancelled",
                                    ));
                                }
                                Ok(())
                            })),
                            &mut token,
                        )?;
                        return Ok(());
                    }
                    let started_app = app.clone();
                    core.add_NavigationStarting(
                        &NavigationStartingEventHandler::create(Box::new(move |_, args| {
                            if let Some(args) = args {
                                let mut id = 0;
                                args.NavigationId(&mut id)?;
                                let host = started_app.state::<BrowserHost>();
                                let mut state = host.state.lock().unwrap();
                                if state.engine_navigations.len() > 16 {
                                    state.engine_navigations.clear();
                                }
                                let navigation = state.navigation.clone();
                                state.engine_navigations.insert(id, navigation);
                            }
                            Ok(())
                        })),
                        &mut token,
                    )?;
                    let completed_app = app.clone();
                    core.add_NavigationCompleted(
                        &NavigationCompletedEventHandler::create(Box::new(move |_, args| {
                            if let Some(args) = args {
                                let mut id = 0;
                                let mut success = BOOL(0);
                                args.NavigationId(&mut id)?;
                                args.IsSuccess(&mut success)?;
                                let host = completed_app.state::<BrowserHost>();
                                let navigation = {
                                    let mut state = host.state.lock().unwrap();
                                    let Some(navigation) = state.engine_navigations.remove(&id)
                                    else {
                                        return Ok(());
                                    };
                                    if navigation != state.navigation || state.closed {
                                        return Ok(());
                                    }
                                    state.loading = false;
                                    if !success.as_bool() {
                                        state.paused = true;
                                        state.page = None;
                                        state.revision = None;
                                    }
                                    navigation
                                };
                                host_event(
                                    &completed_app,
                                    if success.as_bool() {
                                        "load_finished"
                                    } else {
                                        "load_failed"
                                    },
                                    if success.as_bool() {
                                        None
                                    } else {
                                        Some("load_failed")
                                    },
                                );
                                if success.as_bool() {
                                    schedule_capture(completed_app.clone(), navigation);
                                }
                            }
                            Ok(())
                        })),
                        &mut token,
                    )?;
                    let history_app = app.clone();
                    core.add_HistoryChanged(
                        &HistoryChangedEventHandler::create(Box::new(move |core, _| {
                            if let Some(core) = core {
                                let mut back = BOOL(0);
                                let mut forward = BOOL(0);
                                core.CanGoBack(&mut back)?;
                                core.CanGoForward(&mut forward)?;
                                {
                                    let host = history_app.state::<BrowserHost>();
                                    let mut state = host.state.lock().unwrap();
                                    state.back = back.as_bool();
                                    state.forward = forward.as_bool();
                                }
                                host_event(&history_app, "history_changed", None);
                            }
                            Ok(())
                        })),
                        &mut token,
                    )?;
                    let failed_app = app.clone();
                    core.add_ProcessFailed(
                        &ProcessFailedEventHandler::create(Box::new(move |_, _| {
                            {
                                let host = failed_app.state::<BrowserHost>();
                                let mut state = host.state.lock().unwrap();
                                state.paused = true;
                                state.loading = false;
                                state.page = None;
                                state.revision = None;
                            }
                            host_event(&failed_app, "crashed", Some("load_failed"));
                            Ok(())
                        })),
                        &mut token,
                    )?;
                    Ok(())
                }
            })();
            let _ = tx.send(result.is_ok());
        })
        .map_err(|_| browser_error("internal"))?;
    if !rx.await.map_err(|_| browser_error("internal"))? {
        return Err(browser_error("internal"));
    }
    Ok(())
}

#[cfg(not(target_os = "windows"))]
async fn install_native_guards(
    _app: &tauri::AppHandle,
    _webview: &tauri::Webview,
) -> Result<(), BrowserCommandError> {
    Err(browser_error("not_ready"))
}

async fn finish_auth_popup(app: tauri::AppHandle, id: String, code: &'static str) {
    let navigation = {
        let host = app.state::<BrowserHost>();
        let mut state = host.state.lock().unwrap();
        if state.popup_id.as_deref() != Some(&id) {
            return;
        }
        state.popup_id = None;
        state.popup_permit = None;
        state.paused = true;
        state.navigation.clone()
    };
    if let Some(window) = app.get_webview_window("browsing-auth") {
        if window.close().is_err() {
            let host = app.state::<BrowserHost>();
            host.state.lock().unwrap().popup_id = Some(id);
            host_event(&app, "popup_result", Some("failed"));
            return;
        }
    }
    host_event(&app, "popup_result", Some(code));
    // This only probes the opener. A tainted origin still requires explicit authorization.
    schedule_capture(app, navigation);
}

#[cfg(target_os = "windows")]
fn open_auth_popup(
    app: &tauri::AppHandle,
    url: tauri::Url,
    features: tauri::webview::NewWindowFeatures,
) -> tauri::webview::NewWindowResponse<tauri::Wry> {
    let id = {
        let host = app.state::<BrowserHost>();
        let mut state = host.state.lock().unwrap();
        match state.take_popup_permit(Instant::now()) {
            Ok(id) => id,
            Err(_) => {
                drop(state);
                host_event(app, "popup_result", Some("cancelled"));
                return tauri::webview::NewWindowResponse::Deny;
            }
        }
    };
    let result = (|| -> Result<tauri::WebviewWindow, BrowserCommandError> {
        let origin = {
            let host = app.state::<BrowserHost>();
            let state = host.state.lock().unwrap();
            tauri::Url::parse(&state.url)
                .map_err(|_| browser_error("not_ready"))?
                .origin()
                .ascii_serialization()
        };
        // Wry invokes this on its deferred main-thread message, not inside the COM callback.
        tauri::async_runtime::block_on(revoke(app, &origin, None))?;
        let url = tauri::async_runtime::block_on(preflight(url.as_str()))?;
        let title_origin = std::sync::Arc::new(Mutex::new(url.origin().ascii_serialization()));
        let nav_title = title_origin.clone();
        let title = title_origin.clone();
        let nav_app = app.clone();
        let nav_id = id.clone();
        let initial_blank = std::sync::atomic::AtomicBool::new(true);
        let window = tauri::WebviewWindowBuilder::new(
            app,
            "browsing-auth",
            tauri::WebviewUrl::External("about:blank".parse().unwrap()),
        )
        .window_features(features)
        .devtools(false)
        .disable_drag_drop_handler()
        .title(url.origin().ascii_serialization())
        .visible(false)
        .on_navigation(move |target| {
            if target.as_str() == "about:blank"
                && initial_blank.swap(false, std::sync::atomic::Ordering::SeqCst)
            {
                return true;
            }
            initial_blank.store(false, std::sync::atomic::Ordering::SeqCst);
            if tauri::async_runtime::block_on(preflight(target.as_str())).is_err() {
                tauri::async_runtime::spawn(finish_auth_popup(
                    nav_app.clone(),
                    nav_id.clone(),
                    "unsupported",
                ));
                return false;
            }
            *nav_title.lock().unwrap() = target.origin().ascii_serialization();
            if let Some(window) = nav_app.get_webview_window("browsing-auth") {
                let _ = window.set_title(&target.origin().ascii_serialization());
            }
            true
        })
        .on_document_title_changed(move |window, _| {
            let _ = window.set_title(&title.lock().unwrap());
        })
        .on_new_window(|_, _| tauri::webview::NewWindowResponse::Deny)
        .on_download(|_, _| false)
        .build()
        .map_err(|_| browser_error("popup_unsupported"))?;
        let native = app
            .get_webview("browsing-auth")
            .ok_or_else(|| browser_error("internal"))?;
        tauri::async_runtime::block_on(install_native_guards(app, &native))?;
        #[cfg(test)]
        if let (Ok(origin), Ok(cert)) = (
            std::env::var("NYX_OAUTH_FIXTURE_ORIGIN"),
            std::env::var("NYX_OAUTH_FIXTURE_CERT"),
        ) {
            tauri::async_runtime::block_on(browsing_tests::fixture_guards(&native, origin, cert));
        }
        let closed_app = app.clone();
        let closed_id = id.clone();
        window.on_window_event(move |event| {
            if matches!(event, tauri::WindowEvent::Destroyed) {
                tauri::async_runtime::spawn(finish_auth_popup(
                    closed_app.clone(),
                    closed_id.clone(),
                    "cancelled",
                ));
            }
        });
        window.show().map_err(|_| browser_error("internal"))?;
        Ok(window)
    })();
    match result {
        Ok(window) => {
            let timeout_app = app.clone();
            tauri::async_runtime::spawn(async move {
                let deadline = Instant::now() + Duration::from_secs(120);
                loop {
                    tokio::time::sleep(Duration::from_millis(250)).await;
                    if timeout_app
                        .state::<BrowserHost>()
                        .state
                        .lock()
                        .unwrap()
                        .popup_id
                        .as_deref()
                        != Some(&id)
                    {
                        break;
                    }
                    if Instant::now() >= deadline {
                        finish_auth_popup(timeout_app, id, "timeout").await;
                        break;
                    }
                }
            });
            tauri::webview::NewWindowResponse::Create { window }
        }
        Err(_) => {
            tauri::async_runtime::spawn(finish_auth_popup(app.clone(), id, "failed"));
            tauri::webview::NewWindowResponse::Deny
        }
    }
}

#[tauri::command]
async fn browser_create(
    webview: tauri::Webview,
    app: tauri::AppHandle,
    initial_url: String,
    bounds: BrowserBounds,
) -> Result<BrowserCommandResult, BrowserCommandError> {
    check_caller(&webview)?;
    let bounds = bounds.validate()?;
    let url = preflight(&initial_url).await?;
    if !cfg!(target_os = "windows") {
        return Err(browser_error("not_ready"));
    }
    let host = app.state::<BrowserHost>();
    let _gate = host.gate.lock().await;
    if app.get_webview("browsing").is_some() {
        return Ok(host.state.lock().unwrap().result());
    }
    let secret = host
        .secret
        .as_deref()
        .ok_or_else(|| browser_error("backend_unavailable"))?;
    let response = bridge(&host, secret, "sessions", json!({})).await?;
    let session = response["session"]["id"]
        .as_str()
        .ok_or_else(|| browser_error("internal"))?;
    let token = response["bridge_token"]
        .as_str()
        .ok_or_else(|| browser_error("internal"))?;
    *host.state.lock().unwrap() = BrowserState {
        session: session.into(),
        token: token.into(),
        paused: true,
        ..Default::default()
    };
    let profile = app
        .path()
        .app_data_dir()
        .map_err(|_| browser_error("internal"))?
        .join("browser-profile");
    let nav_app = app.clone();
    let load_app = app.clone();
    let title_app = app.clone();
    let popup_app = app.clone();
    let builder = tauri::webview::WebviewBuilder::new(
        "browsing",
        tauri::WebviewUrl::External(tauri::Url::parse("about:blank").unwrap()),
    )
    .data_directory(profile)
    .devtools(false)
    .disable_drag_drop_handler()
    .on_navigation(move |target| {
        if target.scheme() == "about" {
            return target.as_str() == "about:blank"
                && nav_app
                    .state::<BrowserHost>()
                    .state
                    .lock()
                    .unwrap()
                    .navigation
                    .is_empty();
        }
        let host = nav_app.state::<BrowserHost>();
        {
            let mut state = host.state.lock().unwrap();
            if state.closed {
                return false;
            }
            if state.permitted_url.as_deref() == Some(target.as_str()) {
                state.permitted_url = None;
                return true;
            }
            let current = tauri::Url::parse(&state.url).ok();
            if browser_url(target.as_str()).is_ok()
                && current.is_some_and(|url| url.origin() == target.origin())
                && state
                    .verified_at
                    .is_some_and(|at| at.elapsed() < Duration::from_secs(30))
            {
                return true;
            }
        }
        let app = nav_app.clone();
        let target = target.to_string();
        tauri::async_runtime::spawn(async move {
            let host = app.state::<BrowserHost>();
            let _gate = host.gate.lock().await;
            if let Err(error) = navigate(&app, &target).await {
                host_event(&app, "load_failed", Some(&error.code));
            }
        });
        false
    })
    .on_page_load(move |_, payload| {
        if matches!(payload.event(), tauri::webview::PageLoadEvent::Started) {
            engine_load_start(&load_app, payload.url());
        }
    })
    .on_document_title_changed(move |_, title| {
        {
            let host = title_app.state::<BrowserHost>();
            let mut state = host.state.lock().unwrap();
            state.title = title
                .split_whitespace()
                .collect::<Vec<_>>()
                .join(" ")
                .chars()
                .take(512)
                .collect();
        }
        host_event(&title_app, "title_changed", None);
    })
    .on_new_window(move |url, features| {
        #[cfg(target_os = "windows")]
        {
            open_auth_popup(&popup_app, url, features)
        }
        #[cfg(not(target_os = "windows"))]
        {
            let _ = (url, features);
            host_event(&popup_app, "popup_result", Some("unsupported"));
            tauri::webview::NewWindowResponse::Deny
        }
    })
    .on_download(|_, _| false);
    let window = app
        .get_window("main")
        .ok_or_else(|| browser_error("not_ready"))?;
    let size = window.inner_size().map_err(|_| browser_error("internal"))?;
    let factor = window
        .scale_factor()
        .map_err(|_| browser_error("internal"))?;
    if f64::from(size.width) / factor < 960.0 {
        window
            .set_size(tauri::LogicalSize::new(
                960.0,
                f64::from(size.height) / factor,
            ))
            .map_err(|_| browser_error("internal"))?;
    }
    let child = window
        .add_child(
            builder,
            tauri::LogicalPosition::new(bounds.x, bounds.y),
            tauri::LogicalSize::new(bounds.width, bounds.height),
        )
        .map_err(|_| browser_error("load_failed"))?;
    child.hide().map_err(|_| browser_error("internal"))?;
    if let Err(error) = install_native_guards(&app, &child).await {
        child.close().map_err(|_| browser_error("internal"))?;
        return Err(error);
    }
    navigate(&app, url.as_str()).await
}

#[tauri::command]
async fn browser_navigate(
    webview: tauri::Webview,
    app: tauri::AppHandle,
    url: String,
) -> Result<BrowserCommandResult, BrowserCommandError> {
    check_caller(&webview)?;
    child(&app)?;
    let host = app.state::<BrowserHost>();
    let _gate = host.gate.lock().await;
    navigate(&app, &url).await
}

#[tauri::command]
async fn browser_capture(
    webview: tauri::Webview,
    app: tauri::AppHandle,
    navigation_id: String,
) -> Result<BrowserCommandResult, BrowserCommandError> {
    check_caller(&webview)?;
    let host = app.state::<BrowserHost>();
    let _gate = host.gate.lock().await;
    capture(&app, &navigation_id, true).await
}

#[tauri::command]
async fn browser_focus(
    webview: tauri::Webview,
    app: tauri::AppHandle,
    navigation_id: String,
    focus_id: String,
) -> Result<BrowserCommandResult, BrowserCommandError> {
    check_caller(&webview)?;
    if uuid::Uuid::parse_str(&focus_id).is_err() {
        return Err(browser_error("invalid_payload"));
    }
    let host = app.state::<BrowserHost>();
    let _gate = host.gate.lock().await;
    let (session, token, page, revision, old_request) = {
        let state = host.state.lock().unwrap();
        if state.navigation != navigation_id {
            return Err(browser_error("stale_navigation"));
        }
        if state.paused || state.loading || state.closed {
            return Err(browser_error("not_ready"));
        }
        (
            state.session.clone(),
            state.token.clone(),
            state
                .page
                .clone()
                .ok_or_else(|| browser_error("not_ready"))?,
            state.revision.ok_or_else(|| browser_error("not_ready"))?,
            state.focus_requests.get(&focus_id).cloned(),
        )
    };
    // Retries keep the original selection, but must not reuse old privacy facts.
    let webview = child(&app)?;
    let probe = match eval_json(&webview, DOM_PROBE).await {
        Ok(probe) => probe,
        Err(error) => {
            let origin = webview
                .url()
                .map_err(|_| browser_error("load_failed"))?
                .origin()
                .ascii_serialization();
            revoke(&app, &origin, Some(&navigation_id)).await?;
            return Err(error);
        }
    };
    if host.state.lock().unwrap().navigation != navigation_id {
        return Err(browser_error("stale_navigation"));
    }
    let url = browser_url(
        probe["url"]
            .as_str()
            .ok_or_else(|| browser_error("load_failed"))?,
    )?;
    let bad_frame = probe["frames"].as_array().is_none_or(|frames| {
        frames.iter().any(|frame| {
            frame
                .as_str()
                .and_then(|value| tauri::Url::parse(value).ok())
                .is_none_or(|url| sensitive_url(&url))
        })
    });
    if sensitive_url(&url) || probe["password"] != false || bad_frame {
        revoke(
            &app,
            &url.origin().ascii_serialization(),
            Some(&navigation_id),
        )
        .await?;
        return Err(browser_error("origin_authorization_required"));
    }
    let script = format!(
        "{DOM_CAPTURE}({})",
        json!({"url":url.as_str(),"frames":probe["frames"]})
    );
    let dom = match eval_json(&webview, &script).await {
        Ok(dom) => dom,
        Err(error) => {
            revoke(
                &app,
                &url.origin().ascii_serialization(),
                Some(&navigation_id),
            )
            .await?;
            return Err(error);
        }
    };
    if host.state.lock().unwrap().navigation != navigation_id {
        return Err(browser_error("stale_navigation"));
    }
    if dom["sensitive"] == true {
        revoke(
            &app,
            &url.origin().ascii_serialization(),
            Some(&navigation_id),
        )
        .await?;
        return Err(browser_error("origin_authorization_required"));
    }
    if dom["url"] != probe["url"] {
        return Err(browser_error("stale_navigation"));
    }
    let request = if let Some(request) = old_request {
        request
    } else {
        let request = json!({"navigation_id":navigation_id,"revision":revision,"focus_id":focus_id,"selected_text":dom["selected"]});
        let mut state = host.state.lock().unwrap();
        if state.navigation != navigation_id {
            return Err(browser_error("stale_navigation"));
        }
        if state.focus_requests.len() >= 20 {
            return Err(browser_error("state_conflict"));
        }
        state.focus_requests.insert(focus_id, request.clone());
        request
    };
    {
        let state = host.state.lock().unwrap();
        if state.navigation != navigation_id || state.paused || state.popup_id.is_some() {
            return Err(browser_error("stale_navigation"));
        }
    }
    bridge(&host, &token, &format!("pages/{page}/focus"), request).await?;
    let state = host.state.lock().unwrap();
    if state.navigation != navigation_id || state.session != session {
        return Err(browser_error("stale_navigation"));
    }
    Ok(state.result())
}

async fn history_move(
    webview: tauri::Webview,
    app: tauri::AppHandle,
    back: bool,
) -> Result<BrowserCommandResult, BrowserCommandError> {
    check_caller(&webview)?;
    let host = app.state::<BrowserHost>();
    let _gate = host.gate.lock().await;
    let allowed = {
        let state = host.state.lock().unwrap();
        if back {
            state.back
        } else {
            state.forward
        }
    };
    if allowed {
        // Freeze backend context before moving. The engine callback assigns the final target URL.
        let url = { host.state.lock().unwrap().url.clone() };
        let url = preflight(&url).await?;
        let (session, token, navigation) = {
            let mut state = host.state.lock().unwrap();
            let navigation = state.start_navigation(&url);
            state.expected_load = true;
            (state.session.clone(), state.token.clone(), navigation)
        };
        bridge(
            &host,
            &token,
            &format!("sessions/{session}/navigations"),
            json!({"navigation_id":navigation}),
        )
        .await?;
        {
            let mut state = host.state.lock().unwrap();
            if state.navigation == navigation {
                state.synced_navigation = Some(navigation.clone());
            }
        }
        host_event(&app, "navigation_started", None);
        child(&app)?
            .eval(if back {
                "history.back()"
            } else {
                "history.forward()"
            })
            .map_err(|_| browser_error("load_failed"))?;
    }
    let result = host.state.lock().unwrap().result();
    Ok(result)
}

#[tauri::command]
async fn browser_back(
    webview: tauri::Webview,
    app: tauri::AppHandle,
) -> Result<BrowserCommandResult, BrowserCommandError> {
    history_move(webview, app, true).await
}
#[tauri::command]
async fn browser_forward(
    webview: tauri::Webview,
    app: tauri::AppHandle,
) -> Result<BrowserCommandResult, BrowserCommandError> {
    history_move(webview, app, false).await
}

#[tauri::command]
async fn browser_reload(
    webview: tauri::Webview,
    app: tauri::AppHandle,
) -> Result<BrowserCommandResult, BrowserCommandError> {
    check_caller(&webview)?;
    let host = app.state::<BrowserHost>();
    let _gate = host.gate.lock().await;
    let url = host.state.lock().unwrap().url.clone();
    navigate(&app, &url).await
}

#[tauri::command]
async fn browser_stop(
    webview: tauri::Webview,
    app: tauri::AppHandle,
) -> Result<BrowserCommandResult, BrowserCommandError> {
    check_caller(&webview)?;
    eval_json(
        &child(&app)?,
        "(() => { try { window.stop(); return {ok:true}; } catch { return {ok:false}; } })()",
    )
    .await?;
    let host = app.state::<BrowserHost>();
    let mut state = host.state.lock().unwrap();
    state.loading = false;
    state.paused = true;
    Ok(state.result())
}

#[tauri::command]
async fn browser_set_bounds(
    webview: tauri::Webview,
    app: tauri::AppHandle,
    bounds: BrowserBounds,
) -> Result<BrowserCommandResult, BrowserCommandError> {
    check_caller(&webview)?;
    let bounds = bounds.validate()?;
    child(&app)?
        .set_bounds(tauri::Rect {
            position: tauri::LogicalPosition::new(bounds.x, bounds.y).into(),
            size: tauri::LogicalSize::new(bounds.width, bounds.height).into(),
        })
        .map_err(|_| browser_error("internal"))?;
    let result = app.state::<BrowserHost>().state.lock().unwrap().result();
    Ok(result)
}

#[tauri::command]
async fn browser_show(
    webview: tauri::Webview,
    app: tauri::AppHandle,
) -> Result<BrowserCommandResult, BrowserCommandError> {
    check_caller(&webview)?;
    child(&app)?.show().map_err(|_| browser_error("internal"))?;
    let result = app.state::<BrowserHost>().state.lock().unwrap().result();
    Ok(result)
}
#[tauri::command]
async fn browser_hide(
    webview: tauri::Webview,
    app: tauri::AppHandle,
) -> Result<BrowserCommandResult, BrowserCommandError> {
    check_caller(&webview)?;
    child(&app)?.hide().map_err(|_| browser_error("internal"))?;
    let result = app.state::<BrowserHost>().state.lock().unwrap().result();
    Ok(result)
}

#[tauri::command]
async fn browser_set_auth_mode(
    webview: tauri::Webview,
    app: tauri::AppHandle,
    enabled: bool,
) -> Result<BrowserCommandResult, BrowserCommandError> {
    check_caller(&webview)?;
    let host = app.state::<BrowserHost>();
    let _gate = host.gate.lock().await;
    let origin = {
        let mut state = host.state.lock().unwrap();
        state.auth_mode = enabled;
        tauri::Url::parse(&state.url)
            .map_err(|_| browser_error("not_ready"))?
            .origin()
            .ascii_serialization()
    };
    if enabled {
        revoke(&app, &origin, None).await?;
    } else {
        let navigation = host.state.lock().unwrap().navigation.clone();
        let _ = capture(&app, &navigation, true).await;
    }
    let result = host.state.lock().unwrap().result();
    Ok(result)
}

#[tauri::command]
async fn browser_authorize_origin(
    webview: tauri::Webview,
    app: tauri::AppHandle,
    navigation_id: String,
    origin: String,
) -> Result<BrowserCommandResult, BrowserCommandError> {
    check_caller(&webview)?;
    let host = app.state::<BrowserHost>();
    let _gate = host.gate.lock().await;
    let (session, token) = {
        let state = host.state.lock().unwrap();
        if state.navigation != navigation_id {
            return Err(browser_error("stale_navigation"));
        }
        (state.session.clone(), state.token.clone())
    };
    bridge(
        &host,
        &token,
        &format!("sessions/{session}/origins"),
        json!({"navigation_id":navigation_id,"origin":origin}),
    )
    .await?;
    {
        let mut state = host.state.lock().unwrap();
        if state.navigation != navigation_id {
            return Err(browser_error("stale_navigation"));
        }
        state.granted.insert(origin);
    }
    capture(&app, &navigation_id, true).await
}

#[tauri::command]
async fn browser_revoke_origin(
    webview: tauri::Webview,
    app: tauri::AppHandle,
    origin: String,
) -> Result<BrowserCommandResult, BrowserCommandError> {
    check_caller(&webview)?;
    let host = app.state::<BrowserHost>();
    let _gate = host.gate.lock().await;
    revoke(&app, &origin, None).await?;
    let result = host.state.lock().unwrap().result();
    Ok(result)
}

#[tauri::command]
async fn browser_allow_auth_popup(
    webview: tauri::Webview,
    app: tauri::AppHandle,
) -> Result<BrowserCommandResult, BrowserCommandError> {
    check_caller(&webview)?;
    #[cfg(not(target_os = "windows"))]
    {
        let _ = app;
        Err(browser_error("popup_unsupported"))
    }
    #[cfg(target_os = "windows")]
    {
        child(&app)?;
        let host = app.state::<BrowserHost>();
        let mut state = host.state.lock().unwrap();
        if state.closed || state.popup_id.is_some() {
            return Err(browser_error("popup_not_allowed"));
        }
        state.popup_permit = Some(Instant::now() + Duration::from_secs(10));
        Ok(state.result())
    }
}

#[tauri::command]
async fn browser_close(
    webview: tauri::Webview,
    app: tauri::AppHandle,
) -> Result<BrowserCommandResult, BrowserCommandError> {
    check_caller(&webview)?;
    let host = app.state::<BrowserHost>();
    let _gate = host.gate.lock().await;
    let (session, token) = {
        let mut state = host.state.lock().unwrap();
        state.paused = true;
        state.page = None;
        state.revision = None;
        state.granted.clear();
        state.closed = true;
        state.popup_permit = None;
        state.popup_id = None;
        (state.session.clone(), state.token.clone())
    };
    if let Some(child) = app.get_webview("browsing") {
        child.close().map_err(|_| browser_error("internal"))?;
    }
    if let Some(popup) = app.get_webview_window("browsing-auth") {
        popup.close().map_err(|_| browser_error("internal"))?;
    }
    bridge(
        &host,
        &token,
        &format!("sessions/{session}/close"),
        json!({}),
    )
    .await?;
    let result = host.state.lock().unwrap().result();
    Ok(result)
}

#[tauri::command]
async fn browser_clear_data(
    webview: tauri::Webview,
    app: tauri::AppHandle,
) -> Result<BrowserCommandResult, BrowserCommandError> {
    check_caller(&webview)?;
    let host = app.state::<BrowserHost>();
    let _gate = host.gate.lock().await;
    if !host.state.lock().unwrap().closed || app.get_webview("browsing").is_some() {
        return Err(browser_error("state_conflict"));
    }
    let profile = app
        .path()
        .app_data_dir()
        .map_err(|_| browser_error("internal"))?
        .join("browser-profile");
    if profile.exists() {
        std::fs::remove_dir_all(&profile).map_err(|_| browser_error("internal"))?;
    }
    let result = host.state.lock().unwrap().result();
    Ok(result)
}

#[cfg(target_os = "windows")]
fn desktop_presence() -> Result<(u64, String), String> {
    use windows_sys::Win32::System::SystemInformation::GetTickCount;
    use windows_sys::Win32::UI::Input::KeyboardAndMouse::{GetLastInputInfo, LASTINPUTINFO};
    use windows_sys::Win32::UI::WindowsAndMessaging::{
        GetForegroundWindow, GetWindowTextLengthW, GetWindowTextW,
    };

    unsafe {
        let mut input = LASTINPUTINFO {
            cbSize: std::mem::size_of::<LASTINPUTINFO>() as u32,
            dwTime: 0,
        };
        if GetLastInputInfo(&mut input) == 0 {
            return Err("无法读取系统最后输入时间".to_owned());
        }
        let idle_ms = GetTickCount().wrapping_sub(input.dwTime) as u64;

        let foreground = GetForegroundWindow();
        let length = GetWindowTextLengthW(foreground);
        if length <= 0 {
            return Ok((idle_ms, String::new()));
        }
        let mut buffer = vec![0_u16; length as usize + 1];
        let copied = GetWindowTextW(foreground, buffer.as_mut_ptr(), buffer.len() as i32);
        let title = String::from_utf16_lossy(&buffer[..copied.max(0) as usize]);
        Ok((idle_ms, title))
    }
}

#[cfg(not(target_os = "windows"))]
fn desktop_presence() -> Result<(u64, String), String> {
    Err("此平台不支持原生输入采样".to_owned())
}

#[tauri::command]
fn sample_presence() -> Result<(u64, String), String> {
    desktop_presence()
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    #[cfg(debug_assertions)]
    let secret = std::env::var("NYX_BROWSER_BOOTSTRAP_SECRET").ok();
    std::env::remove_var("NYX_BROWSER_BOOTSTRAP_SECRET");
    #[cfg(not(debug_assertions))]
    let secret = {
        let mut bytes = [0u8; 32];
        getrandom::fill(&mut bytes).expect("desktop bootstrap entropy");
        Some(
            bytes
                .iter()
                .map(|byte| format!("{byte:02x}"))
                .collect::<String>(),
        )
    };
    let host = BrowserHost {
        secret,
        client: reqwest::Client::builder()
            .timeout(Duration::from_secs(5))
            .redirect(reqwest::redirect::Policy::none())
            .no_proxy()
            .build()
            .expect("fixed browser HTTP client"),
        state: Mutex::new(BrowserState::default()),
        gate: tokio::sync::Mutex::new(()),
    };
    let app = tauri::Builder::default()
        .manage(host)
        .manage(Mutex::new(None::<std::process::Child>))
        .setup(|app| {
            #[cfg(not(debug_assertions))]
            {
                use std::net::{SocketAddr, TcpStream};
                use std::process::{Command, Stdio};

                let address: SocketAddr = "127.0.0.1:8000".parse().unwrap();
                if TcpStream::connect_timeout(&address, Duration::from_millis(200)).is_ok() {
                    return Err("Backend port 8000 is already occupied".into());
                }
                let executable = std::env::current_exe()?;
                let folder = executable.parent().ok_or("Missing application directory")?;
                let data = app.path().app_data_dir()?;
                std::fs::create_dir_all(&data)?;
                let log = std::fs::File::create(data.join("backend.log"))?;
                let mut command = Command::new(folder.join(if cfg!(windows) {
                    "nyx-backend.exe"
                } else {
                    "nyx-backend"
                }));
                command
                    .current_dir(data)
                    .env(
                        "NYX_BROWSER_BOOTSTRAP_SECRET",
                        app.state::<BrowserHost>().secret.as_ref().unwrap(),
                    )
                    .stdin(Stdio::piped())
                    .stdout(log.try_clone()?)
                    .stderr(log);
                #[cfg(target_os = "windows")]
                {
                    use std::os::windows::process::CommandExt;
                    command.creation_flags(0x08000000); // CREATE_NO_WINDOW
                }
                let mut child = command.spawn()?;
                let deadline = Instant::now() + Duration::from_secs(90);
                loop {
                    if child.try_wait()?.is_some() {
                        return Err("Packaged backend failed; see backend.log".into());
                    }
                    if TcpStream::connect_timeout(&address, Duration::from_millis(200)).is_ok() {
                        break;
                    }
                    if Instant::now() > deadline {
                        drop(child.stdin.take());
                        let _ = child.kill();
                        let _ = child.wait();
                        return Err("Packaged backend startup timed out; see backend.log".into());
                    }
                    std::thread::sleep(Duration::from_millis(100));
                }
                *app.state::<Mutex<Option<std::process::Child>>>()
                    .lock()
                    .unwrap() = Some(child);
            }
            #[cfg(debug_assertions)]
            let _ = app;
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            sample_presence,
            browser_create,
            browser_navigate,
            browser_back,
            browser_forward,
            browser_reload,
            browser_stop,
            browser_set_bounds,
            browser_show,
            browser_hide,
            browser_capture,
            browser_focus,
            browser_set_auth_mode,
            browser_authorize_origin,
            browser_revoke_origin,
            browser_allow_auth_popup,
            browser_close,
            browser_clear_data
        ])
        .build(tauri::generate_context!())
        .expect("error while running tauri application");
    app.run(|app, event| {
        if matches!(&event, tauri::RunEvent::WindowEvent {label, event: tauri::WindowEvent::Destroyed, ..} if label == "main") {
            if let Some(window) = app.get_webview_window("browsing-auth") { let _ = window.close(); }
            app.exit(0);
        }
        if matches!(event, tauri::RunEvent::Exit) {
            if let Some(mut child) = app
                .state::<Mutex<Option<std::process::Child>>>()
                .lock()
                .unwrap()
                .take()
            {
                drop(child.stdin.take());
                let deadline = Instant::now() + Duration::from_secs(35);
                loop {
                    if child.try_wait().ok().flatten().is_some() {
                        break;
                    }
                    if Instant::now() > deadline {
                        let _ = child.kill();
                        let _ = child.wait();
                        break;
                    }
                    std::thread::sleep(Duration::from_millis(100));
                }
            }
        }
    });
}

#[cfg(test)]
mod browsing_tests {
    use super::*;

    #[cfg(target_os = "windows")]
    fn blank_context() -> tauri::Context<tauri::Wry> {
        let mut context = tauri::generate_context!();
        context.config_mut().app.windows[0].url =
            tauri::WebviewUrl::External("about:blank".parse().unwrap());
        context
    }

    #[test]
    fn navigation_only_accepts_public_https_without_credentials() {
        assert!(browser_url("example.com/docs").is_ok());
        for input in [
            "http://example.com",
            "https://u:p@example.com",
            "https://localhost",
            "https://127.0.0.1",
            "https://10.0.0.1",
            "file:///tmp/a",
        ] {
            assert!(browser_url(input).is_err(), "{input}");
        }
    }

    #[test]
    fn url_privacy_signals_are_exact_and_decode_once() {
        for input in [
            "https://example.com/LOGIN",
            "https://example.com/a%2Fsignin",
            "https://example.com/?%63ode=secret",
            "https://example.com/#state=secret",
            "https://example.com/bank",
            "https://example.com/%FF",
            "https://example.com/%zz",
            "https://example.com/pa%C3%9Fword",
            "https://example.com/regi%EF%AC%86er",
            "https://example.com/#%EF%AC%86ate=secret",
        ] {
            assert!(sensitive_url(&tauri::Url::parse(input).unwrap()));
        }
        for input in [
            "https://example.com/login-guide",
            "https://example.com/%256Cogin",
            "https://example.com/?q=login",
        ] {
            assert!(!sensitive_url(&tauri::Url::parse(input).unwrap()));
        }
    }

    #[test]
    fn reserved_addresses_are_not_public() {
        for input in [
            "0.0.0.0",
            "100.64.0.1",
            "192.0.2.1",
            "198.18.0.1",
            "224.0.0.1",
            "::1",
            "::ffff:127.0.0.1",
            "fc00::1",
            "2001:db8::1",
        ] {
            assert!(!public_ip(input.parse().unwrap()), "{input}");
        }
        assert!(public_ip("1.1.1.1".parse().unwrap()));
        assert!(public_ip("2606:4700:4700::1111".parse().unwrap()));
    }

    #[test]
    fn invalid_bounds_are_rejected() {
        assert!(BrowserBounds {
            x: 0.0,
            y: 0.0,
            width: 100.0,
            height: 100.0
        }
        .validate()
        .is_ok());
        for width in [0.0, -1.0, f64::NAN, f64::INFINITY] {
            assert!(BrowserBounds {
                x: 0.0,
                y: 0.0,
                width,
                height: 100.0
            }
            .validate()
            .is_err());
        }
    }

    #[test]
    fn display_dto_never_serializes_private_host_fields() {
        let mut host = BrowserState::default();
        host.token = "secret-token".into();
        host.url = "https://example.com/a?code=secret#token".into();
        let dto = serde_json::to_string(&host.result()).unwrap();
        assert!(!dto.contains("secret"));
        assert!(!dto.contains("token"));
        assert!(dto.contains("https://example.com/a"));
    }

    #[test]
    fn popup_permit_is_one_use_expiring_and_non_concurrent() {
        let now = Instant::now();
        let mut state = BrowserState::default();
        assert!(state.take_popup_permit(now).is_err());
        state.popup_permit = Some(now + Duration::from_secs(10));
        assert!(state.take_popup_permit(now).is_ok());
        assert!(state.take_popup_permit(now).is_err());
        state.popup_id = None;
        state.popup_permit = Some(now);
        assert!(state
            .take_popup_permit(now + Duration::from_millis(1))
            .is_err());
        assert!(state.popup_id.is_none());
    }

    #[cfg(target_os = "windows")]
    #[tauri::command]
    fn acl_probe(counter: tauri::State<'_, std::sync::atomic::AtomicUsize>) -> bool {
        counter.fetch_add(1, std::sync::atomic::Ordering::SeqCst);
        true
    }

    #[cfg(target_os = "windows")]
    pub(super) async fn fixture_guards(
        webview: &tauri::Webview,
        origin: String,
        certificate: String,
    ) {
        use webview2_com::Microsoft::Web::WebView2::Win32::{
            ICoreWebView2_14, COREWEBVIEW2_PERMISSION_STATE_DENY,
            COREWEBVIEW2_SERVER_CERTIFICATE_ERROR_ACTION_ALWAYS_ALLOW,
        };
        use webview2_com::{
            PermissionRequestedEventHandler, ServerCertificateErrorDetectedEventHandler,
        };
        use windows::core::{Interface, PWSTR};
        let (tx, rx) = tokio::sync::oneshot::channel();
        webview.with_webview(move |platform| {
            unsafe {
                let core = platform.controller().CoreWebView2().unwrap();
                let mut token = 0;
                core.add_PermissionRequested(&PermissionRequestedEventHandler::create(Box::new(|_, args| {
                    if let Some(args) = args { args.SetState(COREWEBVIEW2_PERMISSION_STATE_DENY)?; }
                    Ok(())
                })), &mut token).unwrap();
                let core: ICoreWebView2_14 = core.cast().unwrap();
                core.add_ServerCertificateErrorDetected(&ServerCertificateErrorDetectedEventHandler::create(Box::new(move |_, args| {
                    if let Some(args) = args {
                        let mut uri = PWSTR::null();
                        args.RequestUri(&mut uri)?;
                        let uri_text = uri.to_string()?;
                        windows::Win32::System::Com::CoTaskMemFree(Some(uri.0.cast()));
                        let mut pem = PWSTR::null();
                        args.ServerCertificate()?.ToPemEncoding(&mut pem)?;
                        let pem_text = pem.to_string()?;
                        windows::Win32::System::Com::CoTaskMemFree(Some(pem.0.cast()));
                        if tauri::Url::parse(&uri_text).is_ok_and(|url| url.origin().ascii_serialization() == origin)
                            && pem_text.split_whitespace().eq(certificate.split_whitespace()) {
                            args.SetAction(COREWEBVIEW2_SERVER_CERTIFICATE_ERROR_ACTION_ALWAYS_ALLOW)?;
                        }
                    }
                    Ok(())
                })), &mut token).unwrap();
            }
            let _ = tx.send(());
        }).unwrap();
        tokio::time::timeout(Duration::from_secs(3), rx)
            .await
            .unwrap()
            .unwrap();
    }

    #[cfg(target_os = "windows")]
    #[test]
    #[ignore = "Native layout QA; requires existing Vite"]
    fn narrow_window_activates_browsing_without_child() {
        let mut context = tauri::generate_context!();
        context.config_mut().app.windows[0].title = "Nyx layout fixture".into();
        let (tx, rx) = std::sync::mpsc::channel();
        let app = tauri::Builder::default().any_thread().setup(move |app| {
            let app = app.handle().clone();
            tauri::async_runtime::spawn(async move {
                let window=app.get_webview_window("main").unwrap();
                let main=app.get_webview("main").unwrap();
                let deadline=Instant::now()+Duration::from_secs(90);
                loop {
                    let ready=eval_json(&main,"({ok:true,ready:Array.from(document.querySelectorAll('button')).some(b=>b.textContent.trim()==='浏览')})").await;
                    if ready.is_ok_and(|state| state["ready"]==true) {break;}
                    if Instant::now()>deadline {let _=tx.send(false);app.exit(0);return;}
                    tokio::time::sleep(Duration::from_millis(100)).await;
                }
                let _=eval_json(&main,"({ok:true,clicked:(Array.from(document.querySelectorAll('button')).find(b=>b.textContent.trim()==='浏览')?.click(),true)})").await;
                let passed=loop {
                    let state=eval_json(&main,"({ok:true,browsing:document.querySelector('.browser-view')?.hidden===false})").await;
                    let size=window.inner_size().unwrap();
                    if state.is_ok_and(|state| state["browsing"]==true) && f64::from(size.width)/window.scale_factor().unwrap()>=960.0 {
                        break app.get_webview("browsing").is_none();
                    }
                    if Instant::now()>deadline {break false;}
                    tokio::time::sleep(Duration::from_millis(250)).await;
                };
                // Leave a brief screenshot window after the real UI has expanded.
                tokio::time::sleep(Duration::from_secs(8)).await;
                let _=tx.send(passed);
                app.exit(0);
            });
            Ok(())
        }).build(context).unwrap();
        app.run_return(|_, _| {});
        assert!(rx.recv_timeout(Duration::from_secs(1)).unwrap());
    }

    #[cfg(target_os = "windows")]
    #[test]
    #[ignore = "Run through Python's local HTTPS mock IdP fixture"]
    fn local_https_idp_create_popup() {
        let origin = std::env::var("NYX_OAUTH_FIXTURE_ORIGIN").expect("local fixture origin");
        let certificate =
            std::env::var("NYX_OAUTH_FIXTURE_CERT").expect("pinned temporary fixture CA");
        let profile =
            std::env::temp_dir().join(format!("nyx-oauth-spike-{}", uuid::Uuid::new_v4()));
        let child_profile = profile.clone();
        let (tx, rx) = std::sync::mpsc::channel();
        let app = tauri::Builder::default().any_thread()
        .manage(std::sync::atomic::AtomicUsize::new(0))
        .invoke_handler(tauri::generate_handler![acl_probe])
        .manage(BrowserHost { secret:None, client:reqwest::Client::builder().no_proxy().build().unwrap(), state:Mutex::new(BrowserState {
            session:"fixture".into(),token:"fixture-token".into(),url:format!("{origin}/opener"),
            ..Default::default()
        }), gate:tokio::sync::Mutex::new(()) })
        .setup(move |app| {
            let app = app.handle().clone();
            let popup_app = app.clone();
            tauri::async_runtime::spawn(async move {
                let watchdog = app.clone();
                let deadline = tauri::async_runtime::spawn(async move {
                    tokio::time::sleep(Duration::from_secs(45)).await;
                    watchdog.exit(1);
                });
                let result = async {
                    let window = app.get_window("main").unwrap();
                    let webview = window.add_child(
                        tauri::webview::WebviewBuilder::new("oauth-opener", tauri::WebviewUrl::External("about:blank".parse().unwrap()))
                            .data_directory(child_profile).devtools(false).on_download(|_, _| false)
                            .on_new_window(move |url, features| {
                                open_auth_popup(&popup_app, url, features)
                            }),
                        tauri::LogicalPosition::new(0.0, 0.0), tauri::LogicalSize::new(390.0, 600.0),
                    ).unwrap();
                    fixture_guards(&webview, origin.clone(), certificate).await;
                    webview.navigate(format!("{origin}/opener").parse().unwrap()).unwrap();
                    let until = Instant::now() + Duration::from_secs(15);
                    loop {
                        let ready = eval_json(&webview, "({ok:true,ready:location.pathname==='/opener' && document.readyState==='complete'})").await?;
                        if ready["ready"] == true { break; }
                        if Instant::now() > until { return Err(browser_error("load_failed")); }
                        tokio::time::sleep(Duration::from_millis(100)).await;
                    }
                    webview.eval("window.open('/authorize','withoutPermit')").unwrap();
                    tokio::time::sleep(Duration::from_millis(500)).await;
                    assert!(app.get_webview_window("browsing-auth").is_none());
                    app.state::<BrowserHost>().state.lock().unwrap().granted.insert(origin.clone());
                    app.state::<BrowserHost>().state.lock().unwrap().popup_permit = Some(Instant::now()+Duration::from_secs(10));
                    webview.eval("window.open('/authorize','fixtureLogin')").unwrap();
                    let until = Instant::now() + Duration::from_secs(15);
                    let facts = loop {
                        let facts = eval_json(&webview, "({ok:true,result:window.result||null,cookie:document.cookie})").await?;
                        if facts["result"]["ok"] == true { break facts; }
                        if Instant::now() > until { return Err(browser_error("popup_unsupported")); }
                        tokio::time::sleep(Duration::from_millis(100)).await;
                    };
                    assert_eq!(facts["result"]["denied"], true);
                    assert_eq!(app.state::<std::sync::atomic::AtomicUsize>().load(std::sync::atomic::Ordering::SeqCst),0);
                    tokio::time::sleep(Duration::from_millis(500)).await;
                    assert!(app.get_webview_window("browsing-auth").is_none(), "callback must close native window");
                    let old_id = "finished-popup".to_owned();
                    {
                        let host = app.state::<BrowserHost>();
                        let mut state = host.state.lock().unwrap();
                        assert!(state.popup_id.is_none() && state.paused && !state.granted.contains(&origin));
                        state.popup_permit=Some(Instant::now()+Duration::from_secs(10));
                    }
                    webview.eval("window.open('/refused','refusalFixture')").unwrap();
                    let until=Instant::now()+Duration::from_secs(10);
                    let popup = loop {
                        if let Some(popup)=app.get_webview_window("browsing-auth") { break popup; }
                        if Instant::now()>until { return Err(browser_error("popup_unsupported")); }
                        tokio::time::sleep(Duration::from_millis(100)).await;
                    };
                    tokio::time::sleep(Duration::from_millis(500)).await;
                    assert_eq!(popup.title().unwrap(),origin,"remote title must not impersonate the host");
                    finish_auth_popup(app.clone(),old_id,"timeout").await;
                    assert!(app.get_webview_window("browsing-auth").is_some(),"old timer must not close new popup");
                    let id=app.state::<BrowserHost>().state.lock().unwrap().popup_id.clone().unwrap();
                    finish_auth_popup(app.clone(),id,"cancelled").await;
                    webview.close().unwrap();
                    Ok::<_,BrowserCommandError>(facts)
                }.await;
                deadline.abort();
                let _ = tx.send(result);
                app.exit(0);
            });
            Ok(())
        }).build(blank_context()).unwrap();
        app.run_return(|_, _| {});
        let result = rx
            .recv_timeout(Duration::from_secs(1))
            .expect("Create path deadlocked / timed out");
        let facts = result.expect("Create path failed profile/opener/preflight/guards");
        assert_eq!(facts["result"]["ok"], true);
        assert!(facts["cookie"].as_str().unwrap().contains("nyxFixture=one"));
        // Only this newly created fixture profile is removed.
        std::thread::sleep(Duration::from_millis(300));
        let _ = std::fs::remove_dir_all(profile);
    }

    #[cfg(target_os = "windows")]
    #[test]
    #[ignore = "Windows desktop spike; requires access to public https://example.com"]
    fn remote_webview_denies_app_core_and_plugin_invokes() {
        use std::sync::atomic::{AtomicUsize, Ordering};
        let profile =
            std::env::temp_dir().join(format!("nyx-browser-smoke-{}", uuid::Uuid::new_v4()));
        let child_profile = profile.clone();
        let (tx, rx) = std::sync::mpsc::channel();
        let app = tauri::Builder::default().any_thread()
            .manage(AtomicUsize::new(0))
            .manage(BrowserHost { secret:None, client:reqwest::Client::new(), state:Mutex::new(BrowserState::default()), gate:tokio::sync::Mutex::new(()) })
            .invoke_handler(tauri::generate_handler![acl_probe])
            .setup(move |app| {
                let app = app.handle().clone();
                tauri::async_runtime::spawn(async move {
                    let result = async {
                        let window = app.get_window("main").unwrap();
                        let webview = window.add_child(
                            tauri::webview::WebviewBuilder::new("browsing", tauri::WebviewUrl::External(tauri::Url::parse("about:blank").unwrap()))
                                .data_directory(child_profile).devtools(false)
                                .on_new_window(|_, _| tauri::webview::NewWindowResponse::Deny)
                                .on_download(|_, _| false),
                            tauri::LogicalPosition::new(0.0,0.0), tauri::LogicalSize::new(390.0,600.0),
                        ).map_err(|_| browser_error("internal"))?;
                        install_native_guards(&app, &webview).await?;
                        webview.navigate(preflight("https://example.com").await?).map_err(|_| browser_error("load_failed"))?;
                        let deadline = Instant::now() + Duration::from_secs(30);
                        loop {
                            let ready = eval_json(&webview, "(() => ({ok:true,ready:location.protocol === 'https:' && document.readyState === 'complete'}))()").await?;
                            if ready["ready"] == true { break; }
                            if Instant::now() > deadline { return Err(browser_error("load_failed")); }
                            tokio::time::sleep(Duration::from_millis(200)).await;
                        }
                        webview.eval(r#"(async () => {
                          window.__nyxAclSmoke = [];
                          for (const [command,args] of [['acl_probe',{}],['plugin:webview|set_webview_zoom',{label:'browsing',value:2}],['plugin:fs|read_file',{path:'test'}]]) {
                            try { await window.__TAURI_INTERNALS__.invoke(command,args); window.__nyxAclSmoke.push('ALLOWED'); }
                            catch (e) { window.__nyxAclSmoke.push(String(e)); }
                          }
                        })()"#).map_err(|_| browser_error("load_failed"))?;
                        let deadline = Instant::now() + Duration::from_secs(15);
                        let values = loop {
                            let value = eval_json(&webview, "(() => ({ok:true,results:window.__nyxAclSmoke || []}))()").await?;
                            if value["results"].as_array().is_some_and(|v| v.len() == 3) { break value; }
                            if Instant::now() > deadline { return Err(browser_error("load_failed")); }
                            tokio::time::sleep(Duration::from_millis(100)).await;
                        };
                        let probe = eval_json(&webview, DOM_PROBE).await?;
                        webview.eval("document.body.insertAdjacentHTML('beforeend','<form><span id=nyxFormFixture>private-form-fixture</span><textarea>private-control-fixture</textarea></form>'); const r=document.createRange(); r.selectNodeContents(document.getElementById('nyxFormFixture')); window.getSelection().removeAllRanges(); window.getSelection().addRange(r);").map_err(|_| browser_error("load_failed"))?;
                        let dom = eval_json(&webview, &format!("{DOM_CAPTURE}({})", json!({"url":probe["url"],"frames":probe["frames"]}))).await?;
                        webview.eval("document.body.insertAdjacentHTML('beforeend','<input type=password>')").map_err(|_| browser_error("load_failed"))?;
                        let private = eval_json(&webview, &format!("{DOM_CAPTURE}({})", json!({"url":probe["url"],"frames":probe["frames"]}))).await?;
                        webview.close().map_err(|_| browser_error("internal"))?;
                        Ok::<_, BrowserCommandError>((values, dom, private))
                    }.await;
                    let _ = tx.send(result);
                    app.exit(0);
                });
                Ok(())
            }).build(blank_context()).unwrap();
        let handle = app.handle().clone();
        app.run_return(|_, _| {});
        let result = rx
            .recv_timeout(Duration::from_secs(1))
            .expect("desktop spike stopped without a result");
        let counter = handle.state::<AtomicUsize>().load(Ordering::SeqCst);
        drop(handle);
        // WebView2 releases profile file handles asynchronously after closing.
        for attempt in 0..20 {
            match std::fs::remove_dir_all(&profile) {
                Ok(()) => break,
                Err(error) if error.kind() == std::io::ErrorKind::NotFound => break,
                Err(error) if attempt == 19 => eprintln!("test profile cleanup: {error}"),
                Err(_) => std::thread::sleep(Duration::from_millis(100)),
            }
        }
        let (values, dom, private) = result.expect("remote DOM / ACL spike failed");
        for value in values["results"].as_array().unwrap() {
            assert!(value.as_str().unwrap().contains("not allowed"), "{value}");
        }
        assert_eq!(counter, 0);
        assert!(!dom["text"].as_str().unwrap().is_empty());
        assert!(!dom["text"]
            .as_str()
            .unwrap()
            .contains("private-form-fixture"));
        assert_eq!(dom["selected"], Value::Null);
        assert_eq!(private["sensitive"], true);
        assert!(private.get("text").is_none());
    }
}
