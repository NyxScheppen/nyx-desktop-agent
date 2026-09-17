#[cfg(target_os = "windows")]
fn desktop_presence(active_window_ms: u32) -> (bool, String) {
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
        let input_active = GetLastInputInfo(&mut input) != 0
            && GetTickCount().wrapping_sub(input.dwTime) < active_window_ms;

        let foreground = GetForegroundWindow();
        let length = GetWindowTextLengthW(foreground);
        if length <= 0 {
            return (input_active, String::new());
        }
        let mut buffer = vec![0_u16; length as usize + 1];
        let copied = GetWindowTextW(
            foreground,
            buffer.as_mut_ptr(),
            buffer.len() as i32,
        );
        let title = String::from_utf16_lossy(&buffer[..copied.max(0) as usize]);
        (input_active, title)
    }
}

#[cfg(not(target_os = "windows"))]
fn desktop_presence(_active_window_ms: u32) -> (bool, String) {
    (false, String::new())
}

#[tauri::command]
fn sample_presence(active_window_ms: u32) -> (bool, String) {
    desktop_presence(active_window_ms)
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .invoke_handler(tauri::generate_handler![sample_presence])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
