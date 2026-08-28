; Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
; SPDX-License-Identifier: MIT
;
; GAIA Terminal Hub — Windows setup.
;
; Built with STANDALONE makensis, not electron-builder. electron-builder is an
; Electron packager and the terminal hub is a pair of plain executables; routing
; through it would mean carrying an Electron app's assumptions (asar, an app
; bundle, a chrome-sandbox) for two files that need none of them. The
; conventions this repo already established for the Agent UI installer --
; per-user install, the pinned Lemonade MSI, "leave ~/.gaia alone unless asked"
; -- are followed here; see installer/nsis/installer.nsh for their original
; statement and rationale.
;
; Nothing is downloaded at INSTALL time -- the Lemonade MSI is bundled -- so the
; install itself works offline and behind a proxy. Lemonade's -minimal MSI is a
; bootstrap that fetches its runtime on first run, so running a model still
; needs the network once.
;
; Build:
;   makensis -DVERSION=0.23.0 \
;            -DPAYLOAD_DIR=<dir with gaia-tui.exe, gaia-agent.exe, LICENSE.md> \
;            -DLEMONADE_MSI=<path to lemonade-server-minimal.msi> \
;            -DLEMONADE_VERSION=11.5.0 \
;            -DICON=<path to gaia.ico> \
;            -DOUTFILE=gaia-0.23.0-win-x64-setup.exe \
;            installer/tui/nsis/gaia-setup.nsi

Unicode true

; ─── Required defines ──────────────────────────────────────────────────────
; A missing define expands to an empty string, which would silently produce an
; installer that ships nothing or ships it under the wrong name.
!ifndef VERSION
  !error "VERSION is required: -DVERSION=0.23.0"
!endif
!ifndef PAYLOAD_DIR
  !error "PAYLOAD_DIR is required: the directory holding gaia-tui.exe, gaia-agent.exe and LICENSE.md"
!endif
!ifndef LEMONADE_MSI
  !error "LEMONADE_MSI is required: the pinned lemonade-server-minimal.msi to bundle. This installer is offline by contract, so the MSI must be downloaded and verified by the build, not fetched at install time."
!endif
!ifndef LEMONADE_VERSION
  !error "LEMONADE_VERSION is required: the version of the bundled MSI, shown to the user during install"
!endif
!ifndef ICON
  !error "ICON is required: -DICON=src/gaia/img/gaia.ico"
!endif
!ifndef OUTFILE
  !error "OUTFILE is required: -DOUTFILE=gaia-<version>-win-x64-setup.exe"
!endif

!define PRODUCT_NAME      "GAIA Terminal Hub"
!define PRODUCT_PUBLISHER "Advanced Micro Devices, Inc."
!define PRODUCT_URL       "https://amd-gaia.ai"
!define UNINST_KEY        "Software\Microsoft\Windows\CurrentVersion\Uninstall\GAIATerminalHub"
!define TUI_EXE           "gaia-tui.exe"
!define AGENT_EXE         "gaia-agent.exe"
!define LEMONADE_MSI_NAME "lemonade-server-minimal.msi"

Name "${PRODUCT_NAME} ${VERSION}"
OutFile "${OUTFILE}"
; Per-user by design: no admin prompt, no UAC dialog, nothing written outside
; the user's own profile. A machine-wide install would need elevation for a
; tool one user runs in their own terminal.
RequestExecutionLevel user
InstallDir "$LOCALAPPDATA\Programs\GAIA"
InstallDirRegKey HKCU "Software\GAIA\TerminalHub" "InstallDir"
ShowInstDetails show
ShowUnInstDetails show
SetCompressor /SOLID lzma

; Explorer sorts on this binary field, so a literal 0.0.0.0 would make every
; build report as older than the last. build-setup.sh enforces x.y.z.
VIProductVersion "${VERSION}.0"
VIAddVersionKey "ProductName"     "${PRODUCT_NAME}"
VIAddVersionKey "CompanyName"     "${PRODUCT_PUBLISHER}"
VIAddVersionKey "FileDescription" "${PRODUCT_NAME} Setup"
VIAddVersionKey "FileVersion"     "${VERSION}"
VIAddVersionKey "ProductVersion"  "${VERSION}"
VIAddVersionKey "LegalCopyright"  "Copyright (C) 2025-2026 ${PRODUCT_PUBLISHER}"

!include "MUI2.nsh"
!include "LogicLib.nsh"
!include "FileFunc.nsh"
!include "WinMessages.nsh"

!insertmacro GetSize

!define MUI_ICON   "${ICON}"
!define MUI_UNICON "${ICON}"
!define MUI_ABORTWARNING
!define MUI_FINISHPAGE_RUN "$INSTDIR\${TUI_EXE}"
!define MUI_FINISHPAGE_RUN_TEXT "Start ${PRODUCT_NAME}"
!define MUI_FINISHPAGE_LINK "GAIA documentation"
!define MUI_FINISHPAGE_LINK_LOCATION "${PRODUCT_URL}"

!insertmacro MUI_PAGE_LICENSE "${PAYLOAD_DIR}\LICENSE.md"
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH

!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES

!insertmacro MUI_LANGUAGE "English"

; ─── PATH helpers ──────────────────────────────────────────────────────────
;
; A HKCU\Environment "Path" longer than ${NSIS_MAX_STRLEN} does NOT read back
; truncated: ReadRegStr sets the error flag and yields an EMPTY string, so a
; naive read-append-write REPLACES a long PATH instead of extending it. The
; error flag cannot tell "too long" from "not set", so ReadUserPath resolves
; that with EnumRegValue and every caller refuses what it cannot read whole.
;
; Presence is matched as ";<dir>;" inside ";<PATH>;" so C:\...\GAIA-old never
; counts as C:\...\GAIA.
;
; Emitted twice via macros: NSIS requires "un.<name>" for anything an uninstall
; section calls.

!macro ReadUserPath UN
Function ${UN}ReadUserPath
  ; out: $R0 = 0 read it whole (value may legitimately be empty)
  ;            1 the value exists but is too long for NSIS to hold
  ;      $R1 = the PATH when $R0 is 0
  Push $R3
  Push $R4
  ClearErrors
  ReadRegStr $R1 HKCU "Environment" "Path"
  ${IfNot} ${Errors}
    StrCpy $R0 0
    Pop $R4
    Pop $R3
    Return
  ${EndIf}
  ; Error flag set: either the value is absent, or it overflowed the buffer.
  ; EnumRegValue reports NAMES, which are short, so it answers that safely.
  StrCpy $R1 ""
  StrCpy $R0 0
  StrCpy $R3 0
  ${Do}
    EnumRegValue $R4 HKCU "Environment" $R3
    ${If} $R4 == ""
      ${Break}
    ${EndIf}
    ${If} $R4 == "Path"
      StrCpy $R0 1
      ${Break}
    ${EndIf}
    IntOp $R3 $R3 + 1
  ${Loop}
  Pop $R4
  Pop $R3
FunctionEnd
!macroend
!insertmacro ReadUserPath ""
!insertmacro ReadUserPath "un."

!macro PathSegmentPresent UN
Function ${UN}PathSegmentPresent
  ; in : $R1 = the full PATH, $R2 = the directory to look for
  ; out: $R0 = 1 when present, 0 when not
  Push $R3
  Push $R4
  Push $R5
  Push $R6
  Push $R7
  StrCpy $R3 ";$R1;"
  StrCpy $R4 ";$R2;"
  StrLen $R5 $R4
  StrCpy $R0 0
  StrCpy $R6 0
  ${Do}
    StrCpy $R7 $R3 $R5 $R6
    ${If} $R7 == ""
      ${Break}
    ${EndIf}
    ${If} $R7 == $R4
      StrCpy $R0 1
      ${Break}
    ${EndIf}
    IntOp $R6 $R6 + 1
  ${Loop}
  Pop $R7
  Pop $R6
  Pop $R5
  Pop $R4
  Pop $R3
FunctionEnd
!macroend
!insertmacro PathSegmentPresent ""
!insertmacro PathSegmentPresent "un."

; Only the uninstaller drops a segment, so this one is not generated for the
; installer -- an unused copy there is dead code makensis warns about.
Function un.PathWithoutSegment
  ; in : $R1 = the full PATH, $R2 = the directory to drop
  ; out: $R0 = the PATH with every occurrence of $R2 removed
  ;
  ; Every OTHER segment is copied through verbatim, empty ones included: an empty
  ; PATH entry means "the current directory" to some Windows resolvers, so
  ; collapsing ";;" would change resolution order this uninstaller has no mandate
  ; to touch. $R6 tracks "nothing emitted yet" because an emitted empty segment
  ; is indistinguishable from an empty accumulator.
  Push $R3
  Push $R4
  Push $R5
  Push $R6
  StrCpy $R0 ""
  StrCpy $R3 "$R1;"
  StrCpy $R6 1
  ${Do}
    ${If} $R3 == ""
      ${Break}
    ${EndIf}
    StrCpy $R4 ""
    ${Do}
      StrCpy $R5 $R3 1
      ${If} $R5 == ";"
      ${OrIf} $R5 == ""
        ${Break}
      ${EndIf}
      StrCpy $R4 "$R4$R5"
      StrCpy $R3 $R3 "" 1
    ${Loop}
    StrCpy $R3 $R3 "" 1
    ${If} $R4 != $R2
      ${If} $R6 == 1
        StrCpy $R0 "$R4"
        StrCpy $R6 0
      ${Else}
        StrCpy $R0 "$R0;$R4"
      ${EndIf}
    ${EndIf}
  ${Loop}
  Pop $R6
  Pop $R5
  Pop $R4
  Pop $R3
FunctionEnd

; Tell every running process the environment changed, so a NEW cmd.exe or
; PowerShell picks the PATH up without a sign-out. Already-open shells keep the
; copy they inherited -- that is Windows, not something an installer can fix.
!macro BroadcastEnvChange
  SendMessage ${HWND_BROADCAST} ${WM_WININICHANGE} 0 "STR:Environment" /TIMEOUT=5000
!macroend

; ─── Install ───────────────────────────────────────────────────────────────

Function .onInit
  ; $PLUGINSDIR is where the bundled Lemonade MSI is unpacked, and it does not
  ; exist until something asks for it. MUI2 alone never does -- the Agent UI's
  ; installer.nsh gets away with omitting this only because electron-builder's
  ; generated script calls it first. Without it SetOutPath lands on "" and the
  ; MSI install fails for every user.
  InitPluginsDir
FunctionEnd

Section "GAIA Terminal Hub" SecMain
  SectionIn RO
  SetOutPath "$INSTDIR"

  File "${PAYLOAD_DIR}\${TUI_EXE}"
  File "${PAYLOAD_DIR}\${AGENT_EXE}"
  File "${PAYLOAD_DIR}\LICENSE.md"
  File "/oname=gaia.ico" "${ICON}"

  WriteRegStr HKCU "Software\GAIA\TerminalHub" "InstallDir" "$INSTDIR"
  WriteRegStr HKCU "Software\GAIA\TerminalHub" "Version"    "${VERSION}"

  ; ── Add/Remove Programs ──
  WriteUninstaller "$INSTDIR\Uninstall.exe"
  WriteRegStr   HKCU "${UNINST_KEY}" "DisplayName"          "${PRODUCT_NAME}"
  WriteRegStr   HKCU "${UNINST_KEY}" "DisplayVersion"       "${VERSION}"
  WriteRegStr   HKCU "${UNINST_KEY}" "Publisher"            "${PRODUCT_PUBLISHER}"
  WriteRegStr   HKCU "${UNINST_KEY}" "DisplayIcon"          "$INSTDIR\gaia.ico"
  WriteRegStr   HKCU "${UNINST_KEY}" "URLInfoAbout"         "${PRODUCT_URL}"
  WriteRegStr   HKCU "${UNINST_KEY}" "InstallLocation"      "$INSTDIR"
  WriteRegStr   HKCU "${UNINST_KEY}" "UninstallString"      '"$INSTDIR\Uninstall.exe"'
  WriteRegStr   HKCU "${UNINST_KEY}" "QuietUninstallString" '"$INSTDIR\Uninstall.exe" /S'
  WriteRegDWORD HKCU "${UNINST_KEY}" "NoModify" 1
  WriteRegDWORD HKCU "${UNINST_KEY}" "NoRepair" 1
  ${GetSize} "$INSTDIR" "/S=0K" $0 $1 $2
  WriteRegDWORD HKCU "${UNINST_KEY}" "EstimatedSize" "$0"

  ; ── Shortcuts ──
  ; Both point at gaia-tui.exe. cobra's Explorer guard is disabled in the binary
  ; (tui/internal/cli/root.go); left at its default it would make every one of
  ; these shortcuts print "This is a command line tool" and exit, because a
  ; shortcut launches via Explorer -- exactly the case mousetrap trips on.
  CreateDirectory "$SMPROGRAMS\GAIA"
  CreateShortcut "$SMPROGRAMS\GAIA\${PRODUCT_NAME}.lnk" "$INSTDIR\${TUI_EXE}" "" "$INSTDIR\gaia.ico" 0
  CreateShortcut "$SMPROGRAMS\GAIA\Uninstall ${PRODUCT_NAME}.lnk" "$INSTDIR\Uninstall.exe"
  CreateShortcut "$DESKTOP\${PRODUCT_NAME}.lnk" "$INSTDIR\${TUI_EXE}" "" "$INSTDIR\gaia.ico" 0

  Call AddToUserPath
SectionEnd

!macro RefusePathEdit
  DetailPrint "Leaving your user PATH untouched - Setup cannot edit it safely."
  ; /SD IDOK, or a silent install (/S, GPO, SCCM) blocks forever on a dialog
  ; nobody can see.
  MessageBox MB_OK|MB_ICONEXCLAMATION \
    "GAIA is installed, but Setup could not safely update your PATH: it is longer than the 1023 characters this installer can hold, and rewriting it would lose the rest.$\r$\n$\r$\nAdd this folder to your PATH by hand to run gaia-tui from any terminal:$\r$\n$\r$\n$INSTDIR" \
    /SD IDOK
!macroend

Function AddToUserPath
  Call ReadUserPath
  ${If} $R0 == 1
    !insertmacro RefusePathEdit
    Return
  ${EndIf}

  StrCpy $R2 "$INSTDIR"
  Call PathSegmentPresent
  ${If} $R0 == 1
    DetailPrint "$INSTDIR is already on your PATH."
    Return
  ${EndIf}

  ; The APPENDED string has to fit too. StrCpy truncates at
  ; ${NSIS_MAX_STRLEN} without complaint, so a PATH that reads back fine can
  ; still lose its tail on the way out. +1 for the ";" separator.
  StrLen $R3 $R1
  StrLen $R5 "$INSTDIR"
  IntOp $R3 $R3 + $R5
  IntOp $R3 $R3 + 1
  ${If} $R3 >= ${NSIS_MAX_STRLEN}
    !insertmacro RefusePathEdit
    Return
  ${EndIf}

  ${If} $R1 == ""
    StrCpy $R4 "$INSTDIR"
  ${Else}
    StrCpy $R4 "$R1;$INSTDIR"
  ${EndIf}
  ; REG_EXPAND_SZ: a user PATH commonly contains %USERPROFILE% and friends, and
  ; rewriting it as a plain string would freeze those at today's values.
  WriteRegExpandStr HKCU "Environment" "Path" "$R4"
  !insertmacro BroadcastEnvChange
  DetailPrint "Added $INSTDIR to your PATH (open a new terminal to pick it up)."
FunctionEnd

Section "-Lemonade" SecLemonade
  ; The local inference server GAIA runs models on. Bundled rather than
  ; downloaded so this installer works offline; $PLUGINSDIR is auto-deleted on
  ; exit, so the MSI does not linger on disk.
  ;
  ; A Lemonade failure does not abort the GAIA install -- both binaries are
  ; still useful and gaia-tui walks the user through setup -- but it is never
  ; swallowed: the real-failure branch raises a dialog naming the exit code and
  ; what to run next. Same contract as installer/nsis/installer.nsh, made
  ; visible rather than log-only.
  SetOutPath "$PLUGINSDIR"
  File "/oname=${LEMONADE_MSI_NAME}" "${LEMONADE_MSI}"
  DetailPrint "Installing Lemonade Server ${LEMONADE_VERSION}..."
  ClearErrors
  ExecWait 'msiexec /i "$PLUGINSDIR\${LEMONADE_MSI_NAME}" /qn /norestart' $0
  ${If} $0 == 0
    DetailPrint "Lemonade Server installed successfully."
  ${ElseIf} $0 == 1638
    ; ERROR_PRODUCT_VERSION — a newer Lemonade is already installed.
    DetailPrint "Lemonade Server: a newer version is already installed (bundled MSI skipped)."
  ${ElseIf} $0 == 3010
    ; ERROR_SUCCESS_REBOOT_REQUIRED — installed; reboot pending.
    DetailPrint "Lemonade Server installed (reboot pending)."
  ${Else}
    DetailPrint "Lemonade Server install FAILED with exit code $0."
    ; /SD IDOK or a silent install blocks here forever on an invisible dialog.
    MessageBox MB_OK|MB_ICONEXCLAMATION \
      "GAIA is installed, but the bundled Lemonade Server did not install (msiexec exit code $0).$\r$\n$\r$\nGAIA needs Lemonade to run models locally. Open a new terminal, run gaia-tui, and it will offer to finish the setup.$\r$\n$\r$\nDetails: ${PRODUCT_URL}" \
      /SD IDOK
  ${EndIf}
  SetOutPath "$INSTDIR"
SectionEnd

; ─── Uninstall ─────────────────────────────────────────────────────────────

Section "Uninstall"
  Delete "$INSTDIR\${TUI_EXE}"
  Delete "$INSTDIR\${AGENT_EXE}"
  Delete "$INSTDIR\LICENSE.md"
  Delete "$INSTDIR\gaia.ico"

  ; The original Uninstall.exe is still exiting here and Windows will not delete
  ; a running image, so a single Delete loses that race. Not /REBOOTOK either:
  ; that needs HKLM rights this per-user installer does not have, so it would
  ; no-op silently and promise a removal that never happens.
  StrCpy $R9 0
  ${Do}
    ClearErrors
    Delete "$INSTDIR\Uninstall.exe"
    ${IfNot} ${FileExists} "$INSTDIR\Uninstall.exe"
      ${Break}
    ${EndIf}
    IntOp $R9 $R9 + 1
    ${If} $R9 >= 20
      DetailPrint "Could not delete $INSTDIR\Uninstall.exe - it is still in use. Everything else is removed; delete that one file by hand."
      ${Break}
    ${EndIf}
    Sleep 250
  ${Loop}
  ; RMDir without /r: removes the directory only when it is empty, so anything
  ; the user put there is left alone rather than deleted on their behalf.
  RMDir "$INSTDIR"

  Delete "$SMPROGRAMS\GAIA\${PRODUCT_NAME}.lnk"
  Delete "$SMPROGRAMS\GAIA\Uninstall ${PRODUCT_NAME}.lnk"
  RMDir  "$SMPROGRAMS\GAIA"
  Delete "$DESKTOP\${PRODUCT_NAME}.lnk"

  DeleteRegKey HKCU "${UNINST_KEY}"
  DeleteRegKey HKCU "Software\GAIA\TerminalHub"

  Call un.RemoveFromUserPath

  ; Lemonade Server is deliberately NOT uninstalled: the MSI registers it as an
  ; independent Windows product other things may be using, and it is removed the
  ; same way whether it arrived here or from a standalone MSI --
  ; `gaia uninstall --purge-lemonade`. Same contract as installer/nsis/installer.nsh.

  ; ~/.gaia holds chats, documents, memory and config. Default No, so data
  ; survives an uninstall unless the user explicitly asks otherwise; /SD IDNO
  ; makes a silent uninstall (Uninstall.exe /S, GPO, SCCM) keep it too.
  MessageBox MB_YESNO|MB_ICONQUESTION \
    "Also remove your GAIA data (chats, documents, memory, config)?$\r$\n$\r$\nThis cannot be undone." \
    /SD IDNO IDNO +2
  RMDir /r "$PROFILE\.gaia"
SectionEnd

Function un.RemoveFromUserPath
  Call un.ReadUserPath
  ; Same refusal as the installer, and for the same reason: a PATH too long to
  ; read whole is one that a rewrite would destroy.
  ${If} $R0 == 1
    DetailPrint "Your user PATH is longer than Setup can hold - leaving it untouched. Remove $INSTDIR from it by hand."
    MessageBox MB_OK|MB_ICONEXCLAMATION \
      "GAIA has been removed, but your PATH still lists:$\r$\n$\r$\n$INSTDIR$\r$\n$\r$\nIt is too long for Setup to edit without losing entries, so remove that one line by hand." \
      /SD IDOK
    Return
  ${EndIf}

  StrCpy $R2 "$INSTDIR"
  Call un.PathSegmentPresent
  ${If} $R0 != 1
    Return
  ${EndIf}

  Call un.PathWithoutSegment
  ${If} $R0 == ""
    DeleteRegValue HKCU "Environment" "Path"
  ${Else}
    WriteRegExpandStr HKCU "Environment" "Path" "$R0"
  ${EndIf}
  !insertmacro BroadcastEnvChange
  DetailPrint "Removed $INSTDIR from your PATH."
FunctionEnd
