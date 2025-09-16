@echo off
setlocal EnableDelayedExpansion

REM Get the GAIA installation directory from environment variable or script location
if defined GAIA_INSTALL_DIR (
    set "INSTALL_DIR=%GAIA_INSTALL_DIR%"
) else (
    REM If GAIA_INSTALL_DIR is not set, use the parent directory of this script's location
    set "INSTALL_DIR=%~dp0.."
)

REM Verify Python directory exists
if not exist "%INSTALL_DIR%\python" (
    echo ERROR: Python directory not found at %INSTALL_DIR%\python
    echo Please ensure GAIA is installed correctly.
    exit /b 1
)

REM Parse command line arguments
set "MODE="
:parse_args
if "%~1"=="" goto end_parse
if /i "%~1"=="--ui" (
    set "MODE=ui"
) else if /i "%~1"=="--cli" (
    set "MODE=cli"
)
shift
goto parse_args
:end_parse

REM Validate mode argument
if not defined MODE (
    echo Usage: %~n0 [--ui^|--cli]
    echo   --ui  Launch GAIA UI mode ^(no console window^)
    echo   --cli Launch GAIA CLI mode ^(with console window^)
    exit /b 1
)

REM Launch GAIA in the appropriate modeS
if "%MODE%"=="ui" (
    REM Use pythonw.exe for UI mode to avoid console window
    start "" "%INSTALL_DIR%\python\Scripts\gaia.exe"
) else (
    REM Use gaia script for CLI mode and keep the window open afterwards
cmd /k "%INSTALL_DIR%\\python\\Scripts\\gaia.exe"
)
