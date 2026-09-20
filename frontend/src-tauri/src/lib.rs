#[cfg(target_os = "windows")]
fn desktop_presence() -> Result<(u64, String), String> {
    use windows_sys::Win32::System::SystemInformation::GetTickCount;
    use windows_sys::Win32::UI::Input::KeyboardAndMouse::{
        GetLastInputInfo, LASTINPUTINFO,
    };
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
        let copied = GetWindowTextW(
            foreground,
            buffer.as_mut_ptr(),
            buffer.len() as i32,
        );
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
    tauri::Builder::default()
        .invoke_handler(tauri::generate_handler![sample_presence])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
