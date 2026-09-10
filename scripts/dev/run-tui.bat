@echo off
REM Launch the GAIA terminal UI from THIS checkout.
REM
REM GAIA_GAIA_AGENT_MODE=dev is the load-bearing line: without it the daemon
REM starts the last PUBLISHED agent build instead of your working tree, so any
REM tool you just added simply will not exist and the agent will say it cannot
REM do the thing you are testing.
setlocal
set REPO=%~dp0..\..\
set PYTHONPATH=%REPO%src;%REPO%hub\agents\gaia\python;%REPO%hub\agents\chat\python
set GAIA_GAIA_AGENT_MODE=dev
cd /d "%REPO%"
if not exist "%REPO%tui\bin\gaia-tui.exe" (
  echo Terminal UI not built. Run:
  echo     cd tui ^&^& go build -o bin/gaia-tui.exe ./cmd/gaia
  exit /b 1
)
"%REPO%tui\bin\gaia-tui.exe" %*
