@echo off
REM Set GAIA_MODE to GENERIC for current session
echo Setting GAIA_MODE to GENERIC...
set "GAIA_MODE=GENERIC"

REM Set GAIA_MODE to GENERIC permanently using setx
setx GAIA_MODE "GENERIC"
echo SUCCESS: Set environment variable GAIA_MODE=GENERIC

REM Verify the current value
echo Current GAIA_MODE: %GAIA_MODE%

echo.
echo To use this mode, run:
echo   gaia
echo.