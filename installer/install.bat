@echo off
setlocal EnableDelayedExpansion

:: Get arguments
set PYTHON_EXE=%1
set INSTALL_DIR=%2
set MODE=%3
set LOG_FILE=%INSTALL_DIR%\gaia_install.log

:: Create log header
echo GAIA Installation Log > "%LOG_FILE%"
echo Timestamp: %date% %time% >> "%LOG_FILE%"
echo Python: %PYTHON_EXE% >> "%LOG_FILE%"
echo Install Dir: %INSTALL_DIR% >> "%LOG_FILE%"
echo Mode: %MODE% >> "%LOG_FILE%"
echo. >> "%LOG_FILE%"

echo Installing GAIA. Please be patient, this can take 5-10 minutes...
echo Installing GAIA. Please be patient, this can take 5-10 minutes... >> "%LOG_FILE%"

:: Update pip and dependencies
echo Updating pip, setuptools, and wheel... >> "%LOG_FILE%"
"%PYTHON_EXE%" -m pip install --upgrade pip setuptools wheel >> "%LOG_FILE%" 2>&1
if %ERRORLEVEL% neq 0 (
    echo ERROR: Failed to update pip and dependencies >> "%LOG_FILE%"
    echo Check %LOG_FILE% for detailed error information
    exit /b 1
)

:: Install GAIA based on mode
if "%MODE%"=="NPU" (
    echo Installing GAIA NPU... >> "%LOG_FILE%"
    "%PYTHON_EXE%" -m pip install "%INSTALL_DIR%"[npu,clip,joker,talk] >> "%LOG_FILE%" 2>&1
) else if "%MODE%"=="HYBRID" (
    echo Installing GAIA Hybrid... >> "%LOG_FILE%"
    "%PYTHON_EXE%" -m pip install "%INSTALL_DIR%"[hybrid,clip,joker,talk] >> "%LOG_FILE%" 2>&1
) else (
    echo Installing GAIA Generic... >> "%LOG_FILE%"
    "%PYTHON_EXE%" -m pip install "%INSTALL_DIR%"[clip,joker,talk] >> "%LOG_FILE%" 2>&1
)

:: Check final error level
if %ERRORLEVEL% neq 0 (
    echo ERROR: Installation failed >> "%LOG_FILE%"
    echo Check %LOG_FILE% for detailed error information
    exit /b 1
)

echo Installation completed successfully >> "%LOG_FILE%"
exit /b 0