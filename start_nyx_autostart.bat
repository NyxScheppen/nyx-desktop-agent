@echo off
setlocal EnableExtensions

rem Windows Startup calls this wrapper; keep the launcher console minimized.
cd /d "%~dp0"
start "Nyx desktop" /min cmd.exe /c call "%~dp0start_nyx.bat" --run --desktop --no-pause

endlocal
exit /b 0
