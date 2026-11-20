#[cfg(any(test, not(debug_assertions)))]
use std::path::{Path, PathBuf};
use std::sync::Mutex;
use std::time::{Duration, Instant};

use tauri::Manager;

#[derive(serde::Deserialize)]
struct RuntimePolicy {
    #[cfg(not(debug_assertions))]
    main: BackendPolicy,
    desktop: DesktopPolicy,
}

#[cfg(not(debug_assertions))]
#[derive(serde::Deserialize)]
struct BackendPolicy {
    #[serde(rename = "_LOCAL_HOST")]
    host: String,
    #[serde(rename = "_LOCAL_PORT")]
    port: u16,
    #[serde(rename = "_LAUNCH_NONCE_ENV")]
    launch_nonce_env: String,
}

#[derive(serde::Deserialize)]
struct DesktopPolicy {
    #[cfg(not(debug_assertions))]
    startup_timeout_seconds: u64,
    shutdown_timeout_seconds: u64,
    #[cfg(not(debug_assertions))]
    probe_timeout_ms: u64,
    poll_interval_ms: u64,
    identity_max_bytes: usize,
    service: String,
    copy: DesktopCopy,
}

#[derive(serde::Deserialize)]
struct DesktopCopy {
    #[cfg(any(test, not(debug_assertions)))]
    missing_application_directory: String,
    #[cfg(not(debug_assertions))]
    backend_port_occupied: String,
    #[cfg(not(debug_assertions))]
    backend_failed: String,
    #[cfg(not(debug_assertions))]
    backend_exited_after_readiness: String,
    #[cfg(not(debug_assertions))]
    backend_startup_timeout: String,
    #[cfg(target_os = "windows")]
    presence_unavailable: String,
    #[cfg(not(target_os = "windows"))]
    presence_unsupported: String,
    application_failed: String,
}

fn runtime_policy() -> &'static RuntimePolicy {
    static POLICY: std::sync::OnceLock<RuntimePolicy> = std::sync::OnceLock::new();
    POLICY.get_or_init(|| {
        serde_json::from_str(include_str!("../../../nyx/settings/runtime.json"))
            .expect("invalid runtime system policy")
    })
}

#[cfg(any(test, not(debug_assertions)))]
#[derive(serde::Deserialize)]
struct BackendIdentity {
    service: String,
    launch_nonce: Option<String>,
}

#[cfg(any(test, not(debug_assertions)))]
fn backend_identity_matches(body: &[u8], launch_nonce: &str) -> bool {
    if body.len() > runtime_policy().desktop.identity_max_bytes {
        return false;
    }
    serde_json::from_slice::<BackendIdentity>(body).is_ok_and(|identity| {
        identity.service == runtime_policy().desktop.service
            && identity.launch_nonce.as_deref() == Some(launch_nonce)
    })
}

#[cfg(not(debug_assertions))]
fn generate_launch_nonce() -> Result<String, std::io::Error> {
    use std::fmt::Write as _;

    let mut bytes = [0_u8; 32];
    getrandom::fill(&mut bytes).map_err(|error| {
        std::io::Error::other(format!("failed to generate launch nonce: {error}"))
    })?;
    let mut nonce = String::with_capacity(bytes.len() * 2);
    for byte in bytes {
        write!(&mut nonce, "{byte:02x}").expect("writing to a String cannot fail");
    }
    Ok(nonce)
}

#[cfg(not(debug_assertions))]
fn probe_backend_identity(client: &reqwest::blocking::Client, launch_nonce: &str) -> bool {
    use std::io::Read as _;

    let response = match client
        .get(format!(
            "http://{}:{}/api/ready",
            runtime_policy().main.host,
            runtime_policy().main.port
        ))
        .send()
    {
        Ok(response) if response.status() == reqwest::StatusCode::OK => response,
        _ => return false,
    };
    if response
        .content_length()
        .is_some_and(|length| length > runtime_policy().desktop.identity_max_bytes as u64)
    {
        return false;
    }
    let mut body = Vec::with_capacity(runtime_policy().desktop.identity_max_bytes);
    if response
        .take(runtime_policy().desktop.identity_max_bytes as u64 + 1)
        .read_to_end(&mut body)
        .is_err()
    {
        return false;
    }
    backend_identity_matches(&body, launch_nonce)
}

#[cfg(any(test, not(debug_assertions)))]
fn packaged_backend_path(executable: &Path) -> Result<PathBuf, &'static str> {
    let folder = executable.parent().ok_or(
        runtime_policy()
            .desktop
            .copy
            .missing_application_directory
            .as_str(),
    )?;
    Ok(folder.join(if cfg!(windows) {
        "nyx-backend.exe"
    } else {
        "nyx-backend"
    }))
}

#[cfg(not(debug_assertions))]
fn start_packaged_backend(
    app: &tauri::App,
) -> Result<std::process::Child, Box<dyn std::error::Error>> {
    use std::net::TcpStream;
    use std::process::{Command, Stdio};

    let address = format!(
        "{}:{}",
        runtime_policy().main.host,
        runtime_policy().main.port
    )
    .parse()?;
    if TcpStream::connect_timeout(
        &address,
        Duration::from_millis(runtime_policy().desktop.probe_timeout_ms),
    )
    .is_ok()
    {
        return Err(runtime_policy()
            .desktop
            .copy
            .backend_port_occupied
            .replace("{port}", &runtime_policy().main.port.to_string())
            .into());
    }

    let executable = std::env::current_exe()?;
    let data = app.path().app_data_dir()?;
    std::fs::create_dir_all(&data)?;
    let log = std::fs::File::create(data.join("backend.log"))?;
    let launch_nonce = generate_launch_nonce()?;
    let client = reqwest::blocking::Client::builder()
        .connect_timeout(Duration::from_millis(
            runtime_policy().desktop.probe_timeout_ms,
        ))
        .timeout(Duration::from_millis(
            runtime_policy().desktop.probe_timeout_ms,
        ))
        .redirect(reqwest::redirect::Policy::none())
        .build()?;
    let mut command = Command::new(packaged_backend_path(&executable)?);
    command
        .current_dir(data)
        .env(&runtime_policy().main.launch_nonce_env, &launch_nonce)
        .stdin(Stdio::piped())
        .stdout(log.try_clone()?)
        .stderr(log);
    #[cfg(target_os = "windows")]
    {
        use std::os::windows::process::CommandExt;
        command.creation_flags(0x08000000); // CREATE_NO_WINDOW
    }

    let mut child = command.spawn()?;
    let deadline =
        Instant::now() + Duration::from_secs(runtime_policy().desktop.startup_timeout_seconds);
    loop {
        if child.try_wait()?.is_some() {
            return Err(runtime_policy().desktop.copy.backend_failed.clone().into());
        }
        if probe_backend_identity(&client, &launch_nonce) {
            if child.try_wait()?.is_none() {
                return Ok(child);
            }
            return Err(runtime_policy()
                .desktop
                .copy
                .backend_exited_after_readiness
                .clone()
                .into());
        }
        if Instant::now() > deadline {
            drop(child.stdin.take());
            let _ = child.kill();
            let _ = child.wait();
            return Err(runtime_policy()
                .desktop
                .copy
                .backend_startup_timeout
                .clone()
                .into());
        }
        std::thread::sleep(Duration::from_millis(
            runtime_policy().desktop.poll_interval_ms,
        ));
    }
}

fn stop_packaged_backend(child: &mut std::process::Child) {
    drop(child.stdin.take());
    let deadline =
        Instant::now() + Duration::from_secs(runtime_policy().desktop.shutdown_timeout_seconds);
    loop {
        if child.try_wait().ok().flatten().is_some() {
            return;
        }
        if Instant::now() > deadline {
            let _ = child.kill();
            let _ = child.wait();
            return;
        }
        std::thread::sleep(Duration::from_millis(
            runtime_policy().desktop.poll_interval_ms,
        ));
    }
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
            return Err(runtime_policy().desktop.copy.presence_unavailable.clone());
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
    Err(runtime_policy().desktop.copy.presence_unsupported.clone())
}

#[tauri::command]
fn sample_presence() -> Result<(u64, String), String> {
    desktop_presence()
}

#[cfg(test)]
mod tests {
    use std::path::Path;

    use super::{backend_identity_matches, packaged_backend_path, runtime_policy};

    #[test]
    fn packaged_backend_is_a_sibling_of_the_desktop_executable() {
        let app = Path::new("C:/Program Files/Nyx/nyx.exe");

        assert_eq!(
            packaged_backend_path(app).unwrap(),
            Path::new("C:/Program Files/Nyx/nyx-backend.exe"),
        );
    }

    #[test]
    fn backend_identity_requires_exact_service_and_launch_nonce() {
        assert!(backend_identity_matches(
            br#"{"service":"nyx-agent","launch_nonce":"nonce"}"#,
            "nonce",
        ));
        assert!(!backend_identity_matches(
            br#"{"service":"nyx-agent","launch_nonce":"other"}"#,
            "nonce",
        ));
        assert!(!backend_identity_matches(
            br#"{"service":"other","launch_nonce":"nonce"}"#,
            "nonce",
        ));
        assert!(!backend_identity_matches(b"not-json", "nonce"));
        assert!(!backend_identity_matches(
            &vec![b'x'; runtime_policy().desktop.identity_max_bytes + 1],
            "nonce",
        ));
    }
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let app = tauri::Builder::default()
        .manage(Mutex::new(None::<std::process::Child>))
        .setup(|app| {
            #[cfg(not(debug_assertions))]
            {
                let child = start_packaged_backend(app)?;
                *app.state::<Mutex<Option<std::process::Child>>>()
                    .lock()
                    .unwrap() = Some(child);
            }
            #[cfg(debug_assertions)]
            let _ = app;
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![sample_presence])
        .build(tauri::generate_context!())
        .expect(&runtime_policy().desktop.copy.application_failed);
    app.run(|app, event| {
        if matches!(event, tauri::RunEvent::Exit) {
            if let Some(mut child) = app
                .state::<Mutex<Option<std::process::Child>>>()
                .lock()
                .unwrap()
                .take()
            {
                stop_packaged_backend(&mut child);
            }
        }
    });
}
