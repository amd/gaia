@echo off
REM Set GAIA_MODE to NPU for current session
echo Setting GAIA_MODE to NPU...
set "GAIA_MODE=NPU"

REM Set GAIA_MODE to NPU permanently using setx
setx GAIA_MODE "NPU"
echo SUCCESS: Set environment variable GAIA_MODE=NPU

REM Verify the current value
echo Current GAIA_MODE: %GAIA_MODE%

echo.
echo To use this mode, run:
echo   gaia
echo.