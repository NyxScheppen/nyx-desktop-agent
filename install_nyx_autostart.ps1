param(
    [switch]$Uninstall,
    [switch]$DesktopShortcut
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$startupDirectory = [Environment]::GetFolderPath("Startup")
$desktopDirectory = [Environment]::GetFolderPath("Desktop")
$startupShortcutPath = Join-Path $startupDirectory "Nyx Desktop.lnk"
$desktopShortcutPath = Join-Path $desktopDirectory "Nyx Desktop.lnk"
$shortcutPath = if ($DesktopShortcut) { $desktopShortcutPath } else { $startupShortcutPath }
$launcherPath = Join-Path $projectRoot "start_nyx.bat"
$packagedExecutablePath = Join-Path $projectRoot "frontend\src-tauri\target\release\nyx.exe"

if ($Uninstall) {
    if (Test-Path -LiteralPath $shortcutPath) {
        Remove-Item -LiteralPath $shortcutPath -Force
        if ($DesktopShortcut) {
            Write-Host "Nyx 桌面快捷方式已移除：$shortcutPath"
        } else {
            Write-Host "Nyx 自动启动已移除：$shortcutPath"
        }
    } else {
        if ($DesktopShortcut) {
            Write-Host "未找到 Nyx 桌面快捷方式。"
        } else {
            Write-Host "未找到 Nyx 自动启动项。"
        }
    }
    exit 0
}

if (-not (Test-Path -LiteralPath $launcherPath)) {
    throw "找不到启动脚本：$launcherPath"
}

$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($shortcutPath)
$shortcut.TargetPath = $launcherPath
$shortcut.Arguments = "--run --desktop --no-pause"
$shortcut.WorkingDirectory = $projectRoot
$shortcut.WindowStyle = 7
$shortcut.Description = "Nyx 桌面端自动启动"

if ($DesktopShortcut) {
    if (Test-Path -LiteralPath $packagedExecutablePath) {
        $shortcut.TargetPath = $packagedExecutablePath
        $shortcut.Arguments = ""
        $shortcut.WorkingDirectory = Split-Path -Parent $packagedExecutablePath
        $shortcut.WindowStyle = 1
        $shortcut.Description = "Nyx 桌面端"
        Write-Host "使用已构建的桌面端：$packagedExecutablePath"
    } else {
        Write-Host "未找到已构建的 nyx.exe，桌面快捷方式将启动桌面开发模式。"
    }
}

$iconPath = Join-Path $projectRoot "frontend\src-tauri\icons\icon.ico"
if (Test-Path -LiteralPath $iconPath) {
    $shortcut.IconLocation = "$iconPath,0"
}
$shortcut.Save()

if ($DesktopShortcut) {
    Write-Host "Nyx 桌面快捷方式已安装：$shortcutPath"
} else {
    Write-Host "Nyx 自动启动已安装：$shortcutPath"
    Write-Host "下次登录 Windows 时会以桌面端模式启动。"
}
