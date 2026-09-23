@echo off
setlocal EnableExtensions

rem Desktop development entrypoint: reuse the shared launcher lifecycle.
cd /d "%~dp0"
call "%~dp0start_nyx.bat" --desktop %*
set "EXIT_CODE=%ERRORLEVEL%"

endlocal
exit /b %EXIT_CODE%
