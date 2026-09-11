@echo off
REM Run the flagship agent from the checkout instead of the installed binary.
REM
REM The TUI resolves `gaia-agent` from PATH before %USERPROFILE%\.gaia\agents\,
REM so putting this directory first is what makes a source change visible in
REM the TUI. run-tui.bat does that for you.
setlocal
set REPO=%~dp0..\..\..
set PYTHONPATH=%REPO%\src;%REPO%\hub\agents\gaia\python;%REPO%\hub\agents\chat\python
if "%GAIA_DEV_PYTHON%"=="" set GAIA_DEV_PYTHON=python
"%GAIA_DEV_PYTHON%" -m gaia_agent.server %*
