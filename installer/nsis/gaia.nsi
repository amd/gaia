; Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
; SPDX-License-Identifier: MIT
;
; GAIA terminal installer — the single .exe you can hand to a colleague.
;
; GAIA runs as two binaries and this lays down both:
;
;   gaia-tui.exe    the terminal UI, installed to a per-user directory that
;                   is added to the user's PATH
;   gaia-agent.exe  the frozen flagship, installed into the hub install root
;                   (~/.gaia/agents/gaia/) where the TUI looks for it
;
; Lemonade and the models are deliberately NOT bundled — they are gigabytes and
; `gaia init` already owns them. See docs/plans/shareable-custom-build.md.
;
; Build with installer/scripts/build-gaia-installer.ps1, which passes every
; define this script requires. Running makensis on it by hand fails at compile
; time with the missing define named, rather than producing an empty installer.

Unicode true
ManifestDPIAware true

!include "MUI2.nsh"
!include "LogicLib.nsh"
!include "FileFunc.nsh"
!include "WinMessages.nsh"
!include "x64.nsh"

; ─── Required defines ───────────────────────────────────────────────────
;
; Every one of these describes payload, so there is no sane default: an
; installer built without them would install nothing, or install the wrong
; version under the right name. Fail at compile time instead.

!ifndef GAIA_VERSION
  !error "GAIA_VERSION is not defined. Build via installer\scripts\build-gaia-installer.ps1 (it reads src/gaia/version.py), or pass -DGAIA_VERSION=x.y.z to makensis."
!endif
!ifndef TUI_BINARY
  !error "TUI_BINARY is not defined — the path to gaia-tui.exe. Build it with `cd tui && go build -o bin/gaia-tui.exe ./cmd/gaia`, then pass -DTUI_BINARY=<path> (build-gaia-installer.ps1 does this for you)."
!endif
!ifndef AGENT_BINARY
  !error "AGENT_BINARY is not defined — the path to gaia-agent.exe. Build it with `python hub/agents/gaia/python/packaging/freeze.py --onefile`, then pass -DAGENT_BINARY=<path> (build-gaia-installer.ps1 does this for you)."
!endif
!ifndef OUT_FILE
  !define OUT_FILE "gaia-setup-${GAIA_VERSION}.exe"
!endif

; ─── Product identity ───────────────────────────────────────────────────

!define PRODUCT_NAME "GAIA"
!define PRODUCT_PUBLISHER "Advanced Micro Devices, Inc."
!define PRODUCT_WEB_SITE "https://amd-gaia.ai"
!define ARP_KEY "Software\Microsoft\Windows\CurrentVersion\Uninstall\GAIA"
!define SETTINGS_KEY "Software\AMD\GAIA"

; The flagship's catalog id and the sentinel filename, mirrored from
; tui/internal/catalog/catalog.go (FlagshipID, SentinelName). The TUI only
; looks under the id it was compiled with, so these two strings are a contract
; with that file, not a naming choice.
!define AGENT_ID "gaia"
!define SENTINEL_NAME ".installed"

; Assets sit next to this script; ${__FILEDIR__} keeps that true no matter
; which directory makensis was invoked from.
!define ASSET_DIR "${__FILEDIR__}"

Name "${PRODUCT_NAME} ${GAIA_VERSION}"
OutFile "${OUT_FILE}"
BrandingText "${PRODUCT_NAME} ${GAIA_VERSION}"
SetCompressor /SOLID lzma
ShowInstDetails show
ShowUnInstDetails show

; Per-user install, no elevation: a colleague who has to raise a ticket for
; admin rights is back to where this installer exists to get them out of.
RequestExecutionLevel user
InstallDir "$LOCALAPPDATA\Programs\GAIA"
InstallDirRegKey HKCU "${SETTINGS_KEY}" "InstallDir"

!ifdef VERSION_4PART
  VIProductVersion "${VERSION_4PART}"
  VIAddVersionKey "ProductName" "${PRODUCT_NAME}"
  VIAddVersionKey "ProductVersion" "${GAIA_VERSION}"
  VIAddVersionKey "FileVersion" "${GAIA_VERSION}"
  VIAddVersionKey "FileDescription" "${PRODUCT_NAME} installer"
  VIAddVersionKey "CompanyName" "${PRODUCT_PUBLISHER}"
  VIAddVersionKey "LegalCopyright" "Copyright (C) 2025-2026 ${PRODUCT_PUBLISHER}"
!endif

; ─── Interface ──────────────────────────────────────────────────────────

!define MUI_ABORTWARNING
!define MUI_ICON "${ASSET_DIR}\icon.ico"
!define MUI_UNICON "${ASSET_DIR}\icon.ico"
!define MUI_WELCOMEFINISHPAGE_BITMAP "${ASSET_DIR}\installer-sidebar.bmp"
!define MUI_UNWELCOMEFINISHPAGE_BITMAP "${ASSET_DIR}\installer-sidebar.bmp"

!define MUI_WELCOMEPAGE_TITLE "Install ${PRODUCT_NAME} ${GAIA_VERSION}"
!define MUI_WELCOMEPAGE_TEXT "This installs the GAIA terminal UI and the GAIA agent for your user account only — no administrator rights needed.$\r$\n$\r$\nThe language model server (Lemonade) and the models are not included. Run 'gaia init' once after this finishes to set those up.$\r$\n$\r$\nClose any GAIA terminal windows before continuing."

!define MUI_FINISHPAGE_TITLE "${PRODUCT_NAME} is installed"
!define MUI_FINISHPAGE_TEXT "Open a NEW terminal window (PATH changes do not reach terminals that are already open) and run:$\r$\n$\r$\n    gaia-tui$\r$\n$\r$\nIf GAIA reports that Lemonade is missing, run 'gaia init' to install it and pull a model."
!define MUI_FINISHPAGE_RUN "$INSTDIR\gaia-tui.exe"
!define MUI_FINISHPAGE_RUN_TEXT "Start ${PRODUCT_NAME} now"
!define MUI_FINISHPAGE_LINK "GAIA documentation"
!define MUI_FINISHPAGE_LINK_LOCATION "${PRODUCT_WEB_SITE}"

!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH

!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES

!insertmacro MUI_LANGUAGE "English"

; ─── Install ────────────────────────────────────────────────────────────

Function .onInit
  ${IfNot} ${RunningX64}
    MessageBox MB_OK|MB_ICONSTOP "${PRODUCT_NAME} ships 64-bit binaries and this is a 32-bit version of Windows.$\r$\n$\r$\nNothing was installed." /SD IDOK
    Abort
  ${EndIf}
FunctionEnd

Section "GAIA" SecCore
  SectionIn RO

  ; Per-user shell folders ($SMPROGRAMS) to match the per-user install.
  SetShellVarContext current

  ; `try` makes a locked target set the error flag instead of tearing down the
  ; install with NSIS's own generic message — a running gaia-tui.exe is the
  ; likely failure here and it deserves an instruction, not an error code.
  SetOverwrite try

  SetOutPath "$INSTDIR"

  DetailPrint "Installing gaia-tui..."
  ClearErrors
  File "/oname=gaia-tui.exe" "${TUI_BINARY}"
  ${If} ${Errors}
    MessageBox MB_OK|MB_ICONSTOP "Could not write $INSTDIR\gaia-tui.exe.$\r$\n$\r$\nIt is almost certainly running. Close every GAIA terminal window, end any leftover gaia-tui.exe in Task Manager, then run this installer again." /SD IDOK
    Abort "gaia-tui.exe is in use — install stopped."
  ${EndIf}

  File "/oname=gaia.ico" "${ASSET_DIR}\icon.ico"
  File "${ASSET_DIR}\gaia-path.ps1"

  ; ── The agent goes where the TUI looks for it ─────────────────────────
  ;
  ; catalog.Find resolves "gaia-agent" through PATH, then the hub install root
  ; ~/.gaia/agents/<id>/ (gated on the sentinel below), then an in-repo build.
  ; Installing into the install root keeps ONE ~90 MB copy on disk and puts it
  ; on the route the TUI is built around.
  StrCpy $R0 "$PROFILE\.gaia\agents\${AGENT_ID}"
  CreateDirectory "$R0"
  SetOutPath "$R0"

  DetailPrint "Installing gaia-agent to $R0..."
  ClearErrors
  File "/oname=gaia-agent.exe" "${AGENT_BINARY}"
  ${If} ${Errors}
    MessageBox MB_OK|MB_ICONSTOP "Could not write $R0\gaia-agent.exe.$\r$\n$\r$\nIt is almost certainly running. Close every GAIA terminal window, end any leftover gaia-agent.exe in Task Manager, then run this installer again." /SD IDOK
    Abort "gaia-agent.exe is in use — install stopped."
  ${EndIf}

  Call WriteSentinel

  SetOutPath "$INSTDIR"

  ; ── PATH ──────────────────────────────────────────────────────────────
  ;
  ; Done in PowerShell rather than ReadRegStr/WriteRegStr because NSIS
  ; truncates strings at NSIS_MAX_STRLEN (1024 in the stock build) with no
  ; error — reading a longer PATH and writing it back would silently destroy
  ; entries the user needs. The helper also preserves REG_EXPAND_SZ, which is
  ; what keeps %USERPROFILE%-style entries working.
  DetailPrint "Adding $INSTDIR to your PATH..."
  nsExec::ExecToLog 'powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "$INSTDIR\gaia-path.ps1" -Action Add -Directory "$INSTDIR"'
  Pop $0
  ${If} $0 != "0"
    MessageBox MB_OK|MB_ICONEXCLAMATION "${PRODUCT_NAME} is installed, but your PATH could not be updated (the helper exited with '$0').$\r$\n$\r$\nAdd this folder to your user PATH by hand, or gaia-tui will only run from its install folder:$\r$\n$\r$\n$INSTDIR" /SD IDOK
  ${Else}
    ; Tell the shell the environment changed; new processes pick it up without
    ; a sign-out. Already-open terminals keep their old PATH regardless.
    SendMessage ${HWND_BROADCAST} ${WM_SETTINGCHANGE} 0 "STR:Environment" /TIMEOUT=5000
  ${EndIf}

  ; ── Start Menu ────────────────────────────────────────────────────────
  ;
  ; SetOutPath sets the shortcut's working directory, so GAIA starts in the
  ; user's home rather than inside its own program folder.
  CreateDirectory "$SMPROGRAMS\${PRODUCT_NAME}"
  SetOutPath "$PROFILE"
  CreateShortcut "$SMPROGRAMS\${PRODUCT_NAME}\${PRODUCT_NAME}.lnk" \
      "$INSTDIR\gaia-tui.exe" "" "$INSTDIR\gaia.ico" 0 SW_SHOWNORMAL "" \
      "GAIA — chat, documents, data and web research in your terminal"
  SetOutPath "$INSTDIR"

  ; ── Registry + uninstaller ────────────────────────────────────────────
  WriteRegStr HKCU "${SETTINGS_KEY}" "InstallDir" "$INSTDIR"
  WriteRegStr HKCU "${SETTINGS_KEY}" "Version" "${GAIA_VERSION}"
  WriteRegStr HKCU "${SETTINGS_KEY}" "AgentDir" "$PROFILE\.gaia\agents\${AGENT_ID}"

  WriteUninstaller "$INSTDIR\Uninstall.exe"

  WriteRegStr HKCU "${ARP_KEY}" "DisplayName" "${PRODUCT_NAME}"
  WriteRegStr HKCU "${ARP_KEY}" "DisplayVersion" "${GAIA_VERSION}"
  WriteRegStr HKCU "${ARP_KEY}" "Publisher" "${PRODUCT_PUBLISHER}"
  WriteRegStr HKCU "${ARP_KEY}" "DisplayIcon" "$INSTDIR\gaia.ico"
  WriteRegStr HKCU "${ARP_KEY}" "InstallLocation" "$INSTDIR"
  WriteRegStr HKCU "${ARP_KEY}" "UninstallString" '"$INSTDIR\Uninstall.exe"'
  WriteRegStr HKCU "${ARP_KEY}" "QuietUninstallString" '"$INSTDIR\Uninstall.exe" /S'
  WriteRegStr HKCU "${ARP_KEY}" "URLInfoAbout" "${PRODUCT_WEB_SITE}"
  WriteRegDWORD HKCU "${ARP_KEY}" "NoModify" 1
  WriteRegDWORD HKCU "${ARP_KEY}" "NoRepair" 1

  ; Both payload directories, so Add/Remove Programs reports the real footprint.
  ${GetSize} "$INSTDIR" "/S=0K" $0 $1 $2
  ${GetSize} "$PROFILE\.gaia\agents\${AGENT_ID}" "/S=0K" $3 $1 $2
  IntOp $0 $0 + $3
  WriteRegDWORD HKCU "${ARP_KEY}" "EstimatedSize" "$0"
SectionEnd

; WriteSentinel writes ~/.gaia/agents/gaia/.installed, the file that makes the
; agent runnable at all.
;
; catalog.findInstalledBinaryIn refuses to hand back a binary from the install
; root unless this file parses as JSON: `gaia-agent` names both the stdio child
; the TUI spawns and the frozen REST sidecar other installers stage into the
; same directory, and spawning the wrong one feeds a uvicorn log to a JSON line
; scanner (#3062). Without the sentinel the TUI reports the agent as an
; unverified install and will not start it.
;
; artifact_kind is deliberately omitted. In that field's vocabulary "binary"
; means a daemon-supervised HTTP sidecar, and catalog.applyInstalledRecord
; switches any agent carrying it to the daemon transport — which for the
; flagship would route it through a daemon this install does not ship. Omitting
; the key leaves the seeded subprocess transport in place, which is what this
; binary actually is.
Function WriteSentinel
  StrCpy $R1 "$PROFILE\.gaia\agents\${AGENT_ID}\${SENTINEL_NAME}"
  ${GetTime} "" "L" $2 $3 $4 $5 $6 $7 $8 ; day month year dayofweek hour min sec

  ClearErrors
  FileOpen $9 "$R1" w
  ${If} ${Errors}
    MessageBox MB_OK|MB_ICONSTOP "Could not write $R1.$\r$\n$\r$\nWithout that file GAIA treats the agent as an unverified install and refuses to start it. Check that you can write to $PROFILE\.gaia and run this installer again." /SD IDOK
    Abort "could not write the install sentinel — install stopped."
  ${EndIf}
  FileWrite $9 '{$\r$\n'
  FileWrite $9 '  "id": "${AGENT_ID}",$\r$\n'
  FileWrite $9 '  "version": "${GAIA_VERSION}",$\r$\n'
  FileWrite $9 '  "language": "python",$\r$\n'
  FileWrite $9 '  "installed_at": "$4-$3-$2T$6:$7:$8",$\r$\n'
  FileWrite $9 '  "executable": "gaia-agent.exe"$\r$\n'
  FileWrite $9 '}$\r$\n'
  FileClose $9
  DetailPrint "Wrote install sentinel $R1"
FunctionEnd

; ─── Uninstall ──────────────────────────────────────────────────────────

Section "Uninstall"
  SetShellVarContext current

  ; NSIS re-runs the uninstaller from %TEMP%, so $INSTDIR is not the working
  ; directory and can be removed. Being explicit costs nothing.
  SetOutPath "$TEMP"

  ; PATH first, while the helper is still on disk.
  ${If} ${FileExists} "$INSTDIR\gaia-path.ps1"
    DetailPrint "Removing $INSTDIR from your PATH..."
    nsExec::ExecToLog 'powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "$INSTDIR\gaia-path.ps1" -Action Remove -Directory "$INSTDIR"'
    Pop $0
    ${If} $0 != "0"
      MessageBox MB_OK|MB_ICONEXCLAMATION "Could not remove $INSTDIR from your PATH (the helper exited with '$0').$\r$\n$\r$\nRemove it by hand: Settings -> System -> About -> Advanced system settings -> Environment Variables -> Path (User)." /SD IDOK
    ${Else}
      SendMessage ${HWND_BROADCAST} ${WM_SETTINGCHANGE} 0 "STR:Environment" /TIMEOUT=5000
    ${EndIf}
  ${Else}
    MessageBox MB_OK|MB_ICONEXCLAMATION "The PATH helper ($INSTDIR\gaia-path.ps1) is missing, so this uninstaller cannot clean your PATH.$\r$\n$\r$\nRemove this entry by hand: $INSTDIR" /SD IDOK
  ${EndIf}

  ; ── The agent and its sentinel ────────────────────────────────────────
  ;
  ; Binary first, sentinel second: a sentinel-less binary left in the install
  ; root is exactly the "unverified install" state the TUI refuses to run, so
  ; a half-finished removal must never end up there.
  StrCpy $R0 "$PROFILE\.gaia\agents\${AGENT_ID}"
  StrCpy $R1 "removed"
  ; Guarded on FileExists because Delete also sets the error flag when the file
  ; was simply not there — an already-clean directory must not raise an alarm.
  ${If} ${FileExists} "$R0\gaia-agent.exe"
    ClearErrors
    Delete "$R0\gaia-agent.exe"
    ${If} ${Errors}
      StrCpy $R1 "locked"
      MessageBox MB_OK|MB_ICONEXCLAMATION "Could not delete $R0\gaia-agent.exe — it is most likely still running.$\r$\n$\r$\nClose every GAIA window and delete that file by hand; the rest of GAIA has been removed." /SD IDOK
    ${EndIf}
  ${EndIf}
  ${If} $R1 == "removed"
    Delete "$R0\${SENTINEL_NAME}"
    ; Empty-only: logs and anything else under this id were put there by
    ; something other than this installer and are left alone.
    RMDir "$R0"
  ${EndIf}

  ; ── Program files ─────────────────────────────────────────────────────
  Delete "$INSTDIR\gaia-tui.exe"
  Delete "$INSTDIR\gaia-path.ps1"
  Delete "$INSTDIR\gaia.ico"
  Delete "$INSTDIR\Uninstall.exe"
  ClearErrors
  RMDir "$INSTDIR"
  ${If} ${Errors}
    DetailPrint "$INSTDIR still contains files that were not installed by GAIA — left in place."
  ${EndIf}

  Delete "$SMPROGRAMS\${PRODUCT_NAME}\${PRODUCT_NAME}.lnk"
  RMDir "$SMPROGRAMS\${PRODUCT_NAME}"

  DeleteRegKey HKCU "${ARP_KEY}"
  DeleteRegKey HKCU "${SETTINGS_KEY}"

  ; Chats, documents and config under $PROFILE\.gaia are the user's, not ours,
  ; and are left alone. `gaia uninstall` is what removes those.
SectionEnd
