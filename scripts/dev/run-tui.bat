@echo off
REM Launch the GAIA terminal UI against the agent in THIS checkout.
REM
REM The TUI spawns the flagship as a child process and finds it by looking for
REM `gaia-agent` on PATH FIRST, then %USERPROFILE%\.gaia\agents\gaia\. So the
REM only way to run your working tree instead of the last installed build is to
REM put a `gaia-agent` earlier on PATH — which is what scripts\dev\bin does.
REM Without it you are testing the installed binary and any tool you just added
REM simply will not exist, no matter what PYTHONPATH says (a frozen binary
REM ignores it).
setlocal
set REPO=%~dp0..\..\
set PYTHONPATH=%REPO%src;%REPO%hub\agents\gaia\python;%REPO%hub\agents\chat\python
set PATH=%REPO%scripts\dev\bin;%PATH%
cd /d "%REPO%"
if not exist "%REPO%tui\bin\gaia-tui.exe" (
  echo Terminal UI not built. Run:
  echo     cd tui ^&^& go build -o bin/gaia-tui.exe ./cmd/gaia
  exit /b 1
)
"%REPO%tui\bin\gaia-tui.exe" %*
