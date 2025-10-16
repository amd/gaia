@REM Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
@REM SPDX-License-Identifier: MIT

@echo off
setlocal EnableDelayedExpansion

:: Get arguments
set PYTHON_EXE=%1
set INSTALL_DIR=%2
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
echo. >> %LOG_FILE%

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

:: Install GAIA using direct installation (not editable mode)
echo Installing GAIA... >> %LOG_FILE%
%PYTHON_EXE% -m pip install --no-warn-script-location "%INSTALL_DIR%[talk,dev,youtube,audio]" >> %LOG_FILE% 2>&1

:: Check final error level
if %ERRORLEVEL% neq 0 (
    echo ERROR: Installation failed >> %LOG_FILE%
    echo Check %LOG_FILE% for detailed error information
    exit /b 1
)

:: This message is used in RunInstaller.ps1 to check if the installation was successful
echo GAIA package installation successful >> %LOG_FILE%
exit /b 0