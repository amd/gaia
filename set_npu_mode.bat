@echo off
REM Set GAIA_MODE to NPU for current session
echo Setting GAIA_MODE to NPU...
set "GAIA_MODE=NPU"

REM Set GAIA_MODE to NPU permanently using setx
setx GAIA_MODE "NPU"
echo SUCCESS: Set environment variable GAIA_MODE=NPU

REM Get installation directory from first parameter
set INSTALL_DIR=%1

REM Create conda environment activation script in the correct installation directory
if defined INSTALL_DIR (
    if not exist "%INSTALL_DIR%\gaia_env\etc\conda\activate.d" mkdir "%INSTALL_DIR%\gaia_env\etc\conda\activate.d" 2>nul
    echo @echo off > "%INSTALL_DIR%\gaia_env\etc\conda\activate.d\env_vars.bat"
    echo set "GAIA_MODE=NPU" >> "%INSTALL_DIR%\gaia_env\etc\conda\activate.d\env_vars.bat"
    echo SUCCESS: Created conda activation script for GAIA_MODE=NPU in %INSTALL_DIR%\gaia_env
) else if defined CONDA_PREFIX (
    REM Fallback to CONDA_PREFIX for backward compatibility
    if not exist "%CONDA_PREFIX%\etc\conda\activate.d" mkdir "%CONDA_PREFIX%\etc\conda\activate.d" 2>nul
    echo @echo off > "%CONDA_PREFIX%\etc\conda\activate.d\env_vars.bat"
    echo set "GAIA_MODE=NPU" >> "%CONDA_PREFIX%\etc\conda\activate.d\env_vars.bat"
    echo SUCCESS: Created conda activation script for GAIA_MODE=NPU
)

REM Verify the current value
echo Current GAIA_MODE: %GAIA_MODE%

echo.
echo To use this mode, run:
echo   gaia
echo.