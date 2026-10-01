#[cfg(any(test, not(debug_assertions)))]
use std::path::{Path, PathBuf};
use std::sync::Mutex;
use std::time::{Duration, Instant};

use tauri::Manager;

#[cfg(not(debug_assertions))]
const BACKEND_ADDRESS: &str = "127.0.0.1:8000";
#[cfg(not(debug_assertions))]
const BACKEND_STARTUP_TIMEOUT: Duration = Duration::from_secs(90);

#[cfg(any(test, not(debug_assertions)))]
fn packaged_backend_path(executable: &Path) -> Result<PathBuf, &'static str> {
    let folder = executable.parent().ok_or("Missing application directory")?;
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

    let address = BACKEND_ADDRESS.parse()?;
    if TcpStream::connect_timeout(&address, Duration::from_millis(200)).is_ok() {
        return Err("Backend port 8000 is already occupied".into());
    }

    let executable = std::env::current_exe()?;
    let data = app.path().app_data_dir()?;
    std::fs::create_dir_all(&data)?;
    let log = std::fs::File::create(data.join("backend.log"))?;
    let mut command = Command::new(packaged_backend_path(&executable)?);
    command
        .current_dir(data)
        .stdin(Stdio::piped())
        .stdout(log.try_clone()?)
        .stderr(log);
    #[cfg(target_os = "windows")]
    {
        use std::os::windows::process::CommandExt;
        command.creation_flags(0x08000000); // CREATE_NO_WINDOW
    }

    let mut child = command.spawn()?;
    let deadline = Instant::now() + BACKEND_STARTUP_TIMEOUT;
    loop {
        if child.try_wait()?.is_some() {
            return Err("Packaged backend failed; see backend.log".into());
        }
        if TcpStream::connect_timeout(&address, Duration::from_millis(200)).is_ok() {
            return Ok(child);
        }
        if Instant::now() > deadline {
            drop(child.stdin.take());
            let _ = child.kill();
            let _ = child.wait();
            return Err("Packaged backend startup timed out; see backend.log".into());
        }
        std::thread::sleep(Duration::from_millis(100));
    }
}

fn stop_packaged_backend(child: &mut std::process::Child) {
    drop(child.stdin.take());
    let deadline = Instant::now() + Duration::from_secs(35);
    loop {
        if child.try_wait().ok().flatten().is_some() {
            return;
        }
        if Instant::now() > deadline {
            let _ = child.kill();
            let _ = child.wait();
            return;
        }
        std::thread::sleep(Duration::from_millis(100));
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

#[cfg(test)]
mod tests {
    use std::path::Path;

    use super::packaged_backend_path;

    #[test]
    fn packaged_backend_is_a_sibling_of_the_desktop_executable() {
        let app = Path::new("C:/Program Files/Nyx/nyx.exe");

        assert_eq!(
            packaged_backend_path(app).unwrap(),
            Path::new("C:/Program Files/Nyx/nyx-backend.exe"),
        );
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
        .expect("error while running tauri application");
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
