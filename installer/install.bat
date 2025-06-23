@REM Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
@REM SPDX-License-Identifier: MIT

@echo off
setlocal EnableDelayedExpansion

:: Get arguments
set PYTHON_EXE=%1
set INSTALL_DIR=%2
set MODE=%3
:: Remove quotes from INSTALL_DIR for LOG_FILE construction
set INSTALL_DIR_UNQUOTED=%INSTALL_DIR:"=%
set LOG_FILE="%INSTALL_DIR_UNQUOTED%\gaia_install.log"

echo Install directory: %INSTALL_DIR%
echo Log file: %LOG_FILE%

:: Set GAIA_INSTALL_DIR environment variable
setx GAIA_INSTALL_DIR %INSTALL_DIR%
set GAIA_INSTALL_DIR=%INSTALL_DIR%

:: Create installation directory if it doesn't exist
if not exist %INSTALL_DIR% mkdir %INSTALL_DIR%

:: Create log header
echo GAIA Installation Log > %LOG_FILE%
echo Timestamp: %date% %time% >> %LOG_FILE%
echo Python: %PYTHON_EXE% >> %LOG_FILE%
echo Install Dir: %INSTALL_DIR% >> %LOG_FILE%
echo Mode: %MODE% >> %LOG_FILE%
echo. >> %LOG_FILE%

:: Validate installation mode
if "%MODE%"=="" (
    echo ERROR: Installation mode not specified >> %LOG_FILE%
    echo ERROR: Mode must be one of: NPU, HYBRID, or GENERIC >> %LOG_FILE%
    exit /b 1
)

if not "%MODE%"=="NPU" if not "%MODE%"=="HYBRID" if not "%MODE%"=="GENERIC" (
    echo ERROR: Invalid installation mode: %MODE% >> %LOG_FILE%
    echo ERROR: Mode must be one of: NPU, HYBRID, or GENERIC >> %LOG_FILE%
    exit /b 1
)

:: Set GAIA_MODE using the appropriate mode-setting script
if "%MODE%"=="NPU" (
    call "%INSTALL_DIR_UNQUOTED%\set_npu_mode.bat" %INSTALL_DIR% >> %LOG_FILE% 2>&1
) else if "%MODE%"=="HYBRID" (
    call "%INSTALL_DIR_UNQUOTED%\set_hybrid_mode.bat" %INSTALL_DIR% >> %LOG_FILE% 2>&1
) else (
    call "%INSTALL_DIR_UNQUOTED%\set_generic_mode.bat" %INSTALL_DIR% >> %LOG_FILE% 2>&1
)

echo Installing GAIA. Please be patient, this can take 5-10 minutes...
echo Installing GAIA. Please be patient, this can take 5-10 minutes... >> %LOG_FILE%

:: Update pip and dependencies
echo Updating pip, setuptools, and wheel... >> %LOG_FILE%
%PYTHON_EXE% -m pip install --upgrade pip setuptools wheel >> %LOG_FILE% 2>&1
if %ERRORLEVEL% neq 0 (
    echo ERROR: Failed to update pip and dependencies >> %LOG_FILE%
    echo Check %LOG_FILE% for detailed error information
    exit /b 1
)

:: Install GAIA based on mode using direct installation (not editable mode)
if "%MODE%"=="NPU" (
    echo Installing GAIA NPU... >> %LOG_FILE%
    %PYTHON_EXE% -m pip install --no-warn-script-location %INSTALL_DIR%[npu,clip,joker,rag,talk] >> %LOG_FILE% 2>&1
) else if "%MODE%"=="HYBRID" (
    echo Installing GAIA Hybrid... >> %LOG_FILE%
    %PYTHON_EXE% -m pip install --no-warn-script-location %INSTALL_DIR%[hybrid,clip,joker,rag,talk] >> %LOG_FILE% 2>&1
) else (
    echo Installing GAIA Generic... >> %LOG_FILE%
    %PYTHON_EXE% -m pip install --no-warn-script-location %INSTALL_DIR%[clip,joker,rag,talk] >> %LOG_FILE% 2>&1
)

:: Check final error level
if %ERRORLEVEL% neq 0 (
    echo ERROR: Installation failed >> %LOG_FILE%
    echo Check %LOG_FILE% for detailed error information
    exit /b 1
)

:: Verify that required settings files exist
echo Verifying settings files... >> %LOG_FILE%
set "INTERFACE_DIR=%INSTALL_DIR%\src\gaia\interface"
set SETTINGS_FOUND=0

:: Check for mode-specific settings file
if "%MODE%"=="NPU" (
    if exist "%INTERFACE_DIR%\npu_settings.json" (
        echo Found NPU settings file >> %LOG_FILE%
        set SETTINGS_FOUND=1
    )
) else if "%MODE%"=="HYBRID" (
    if exist "%INTERFACE_DIR%\hybrid_settings.json" (
        echo Found HYBRID settings file >> %LOG_FILE%
        set SETTINGS_FOUND=1
    )
) else (
    if exist "%INTERFACE_DIR%\generic_settings.json" (
        echo Found GENERIC settings file >> %LOG_FILE%
        set SETTINGS_FOUND=1
    )
)

:: Error if no settings file found
if %SETTINGS_FOUND%==0 (
    echo ERROR: Required settings file not found in "%INTERFACE_DIR%" >> %LOG_FILE%
    echo ERROR: Installation cannot continue without proper settings file >> %LOG_FILE%
    echo Check %LOG_FILE% for detailed error information
    exit /b 1
)

:: This message is used in RunInstaller.ps1 to check if the installation was successful
echo GAIA package installation successful >> %LOG_FILE%
exit /b 0