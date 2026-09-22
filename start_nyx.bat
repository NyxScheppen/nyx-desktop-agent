@echo off
setlocal EnableExtensions

set "NYX_MODE="
set "NYX_NO_PAUSE="
for %%A in (%*) do (
    if /i "%%~A"=="--desktop" set "NYX_MODE=--desktop"
    if /i "%%~A"=="--no-pause" set "NYX_NO_PAUSE=1"
)

rem A double-click normally uses "cmd /c"; relaunch with "cmd /k" so errors
rem and tracebacks remain visible after the launcher exits.
if /i not "%~1"=="--run" (
    start "Nyx launcher" cmd.exe /k call "%~f0" --run %NYX_MODE% %NYX_NO_PAUSE%
    exit /b 0
)

cd /d "%~dp0"
title Nyx development environment

echo [Nyx] Project: %CD%
echo [Nyx] This window stays open. Press Ctrl+C to stop the services.
echo.

set "PYTHON="
if exist "%~dp0.venv\Scripts\python.exe" call :find_python "%~dp0.venv\Scripts\python.exe"
if not defined PYTHON call :find_python python
if not defined PYTHON if exist "%~dp0.runtime" (
    set "PYTHONPATH=%~dp0.runtime;%PYTHONPATH%"
    call :find_python python
)
if not defined PYTHON (
    echo [Nyx] No usable Python with uvicorn was found.
    echo [Nyx] The project environment may be broken or dependencies are missing.
    echo [Nyx] Run: python -m pip install -e .
    goto :failed
)
echo [Nyx] Python: %PYTHON%

set "NPM="
for /f "delims=" %%N in ('where npm.cmd 2^>nul') do if not defined NPM set "NPM=%%N"
if not defined NPM (
    echo [Nyx] Node.js/npm was not found. Please install Node.js first.
    goto :failed
)
echo [Nyx] Checking npm: %NPM%
call "%NPM%" --version >nul 2>&1
if errorlevel 1 (
    echo [Nyx] npm.cmd is not working. Please repair the Node.js installation.
    echo [Nyx] Resolved npm: %NPM%
    goto :failed
)
echo [Nyx] npm: %NPM%

if not exist "%~dp0frontend\node_modules\.bin\vite.cmd" (
    echo [Nyx] Frontend dependencies are missing.
    echo [Nyx] Run: cd frontend ^&^& npm install
    goto :failed
)

echo.
echo [Nyx] Starting backend (8000), waiting for readiness, then frontend (5173)...
"%PYTHON%" dev.py %NYX_MODE%
set "EXIT_CODE=%ERRORLEVEL%"

echo.
if "%EXIT_CODE%"=="0" (
    echo [Nyx] Nyx stopped normally.
) else (
    echo [Nyx] Nyx stopped with exit code %EXIT_CODE%.
    echo [Nyx] The error shown above is the startup log.
)
goto :finished

:find_python
set "CANDIDATE=%~1"
"%CANDIDATE%" -c "import uvicorn" >nul 2>&1
if not errorlevel 1 set "PYTHON=%CANDIDATE%"
exit /b 0

:failed
set "EXIT_CODE=1"
echo.
echo [Nyx] Startup failed. Press any key to close this window.

:finished
if defined NYX_NO_PAUSE (
    endlocal
    exit /b %EXIT_CODE%
)
pause
endlocal
exit /b %EXIT_CODE%
