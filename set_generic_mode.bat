@echo off
REM Set GAIA_MODE to GENERIC for current session
echo Setting GAIA_MODE to GENERIC...
set "GAIA_MODE=GENERIC"

REM Set GAIA_MODE to GENERIC permanently using setx
setx GAIA_MODE "GENERIC"
echo SUCCESS: Set environment variable GAIA_MODE=GENERIC

REM Get installation directory from first parameter
set INSTALL_DIR=%1

REM Create conda environment activation script in the correct installation directory
if defined INSTALL_DIR (
    if not exist "%INSTALL_DIR%\gaia_env\etc\conda\activate.d" mkdir "%INSTALL_DIR%\gaia_env\etc\conda\activate.d" 2>nul
    echo @echo off > "%INSTALL_DIR%\gaia_env\etc\conda\activate.d\env_vars.bat"
    echo set "GAIA_MODE=GENERIC" >> "%INSTALL_DIR%\gaia_env\etc\conda\activate.d\env_vars.bat"
    echo SUCCESS: Created conda activation script for GAIA_MODE=GENERIC in %INSTALL_DIR%\gaia_env
) else if defined CONDA_PREFIX (
    REM Fallback to CONDA_PREFIX for backward compatibility
    if not exist "%CONDA_PREFIX%\etc\conda\activate.d" mkdir "%CONDA_PREFIX%\etc\conda\activate.d" 2>nul
    echo @echo off > "%CONDA_PREFIX%\etc\conda\activate.d\env_vars.bat"
    echo set "GAIA_MODE=GENERIC" >> "%CONDA_PREFIX%\etc\conda\activate.d\env_vars.bat"
    echo SUCCESS: Created conda activation script for GAIA_MODE=GENERIC
)

REM Verify the current value
echo Current GAIA_MODE: %GAIA_MODE%

echo.
echo To use this mode, run:
echo   gaia
echo.