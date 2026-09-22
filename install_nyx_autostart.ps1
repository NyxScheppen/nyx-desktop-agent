param(
    [switch]$Uninstall
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$startupDirectory = [Environment]::GetFolderPath("Startup")
$shortcutPath = Join-Path $startupDirectory "Nyx Desktop.lnk"
$launcherPath = Join-Path $projectRoot "start_nyx_autostart.bat"

if ($Uninstall) {
    if (Test-Path -LiteralPath $shortcutPath) {
        Remove-Item -LiteralPath $shortcutPath -Force
        Write-Host "Nyx 自动启动已移除：$shortcutPath"
    } else {
        Write-Host "未找到 Nyx 自动启动项。"
    }
    exit 0
}

if (-not (Test-Path -LiteralPath $launcherPath)) {
    throw "找不到启动脚本：$launcherPath"
}

$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($shortcutPath)
$shortcut.TargetPath = $launcherPath
$shortcut.WorkingDirectory = $projectRoot
$shortcut.WindowStyle = 7
$shortcut.Description = "Nyx 桌面端自动启动"
$iconPath = Join-Path $projectRoot "frontend\src-tauri\icons\icon.ico"
if (Test-Path -LiteralPath $iconPath) {
    $shortcut.IconLocation = "$iconPath,0"
}
$shortcut.Save()

Write-Host "Nyx 自动启动已安装：$shortcutPath"
Write-Host "下次登录 Windows 时会以桌面端模式启动。"
