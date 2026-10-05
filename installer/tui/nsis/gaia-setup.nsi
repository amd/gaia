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
;   makensis -DVERSION=<__version__ from src/gaia/version.py> \
;            -DPAYLOAD_DIR=<dir with gaia-tui.exe, gaia-agent.exe, LICENSE.md> \
;            -DLEMONADE_MSI=<path to lemonade-server-minimal.msi> \
;            -DLEMONADE_VERSION=<LEMONADE_VERSION from src/gaia/version.py> \
;            -DICON=<path to gaia.ico> \
;            -DFONTS_DIR=<dir staged by installer/tui/fetch_fonts.py> \
;            -DOUTFILE=gaia-<version>-win-x64-setup.exe \
;            installer/tui/nsis/gaia-setup.nsi

Unicode true

; ─── Required defines ──────────────────────────────────────────────────────
; A missing define expands to an empty string, which would silently produce an
; installer that ships nothing or ships it under the wrong name.
!ifndef VERSION
  !error "VERSION is required: -DVERSION=<__version__ from src/gaia/version.py>"
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
!ifndef FONTS_DIR
  !error "FONTS_DIR is required: the IBM Plex Mono faces staged and verified by installer/tui/fetch_fonts.py"
!endif

!define PRODUCT_NAME      "GAIA Terminal Hub"
!define PRODUCT_PUBLISHER "Advanced Micro Devices, Inc."
!define PRODUCT_URL       "https://amd-gaia.ai"
!define UNINST_KEY        "Software\Microsoft\Windows\CurrentVersion\Uninstall\GAIATerminalHub"
!define TUI_EXE           "gaia-tui.exe"
!define AGENT_EXE         "gaia-agent.exe"
!define LEMONADE_MSI_NAME "lemonade-server-minimal.msi"

; Windows Terminal reads per-user fragments from here; settings.json is never touched.
!define WT_FRAGMENT_DIR   "$LOCALAPPDATA\Microsoft\Windows Terminal\Fragments\GAIA"
!define WT_FRAGMENT_FILE  "gaia.json"
; Must match the guid in wt-fragment.json.
!define WT_PROFILE_GUID   "{6b5d7d70-4ed6-471b-b773-f1cafc56246f}"
!define USER_FONTS_DIR    "$LOCALAPPDATA\Microsoft\Windows\Fonts"
!define FONTS_REG         "Software\Microsoft\Windows NT\CurrentVersion\Fonts"
; Which faces THIS setup installed, so uninstall never removes a copy the user had.
!define GAIA_FONTS_KEY    "Software\GAIA\TerminalHub\Fonts"
!define FONT_LICENSE      "IBM-Plex-Mono-OFL.txt"

Name "${PRODUCT_NAME} ${VERSION}"
OutFile "${OUTFILE}"
; Per-user by design: no admin prompt, no UAC dialog, nothing written outside
; the user's own profile. A machine-wide install would need elevation for a
; tool one user runs in their own terminal.
RequestExecutionLevel user
; NOT "...\Programs\GAIA": that is byte-for-byte where the Agent UI's
; electron-builder setup lands (productName "GAIA", perMachine false), and its
; uninstaller removes $INSTDIR recursively.
InstallDir "$LOCALAPPDATA\Programs\GAIA Terminal Hub"
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
!include "WordFunc.nsh"

!insertmacro GetSize

; What the shortcuts and the Finish page launch: wt.exe with the GAIA profile
; when Windows Terminal is present, gaia-tui.exe directly otherwise.
Var LaunchExe
Var LaunchArgs

!define MUI_ICON   "${ICON}"
!define MUI_UNICON "${ICON}"
!define MUI_ABORTWARNING
; Launched through a function so the Finish page opens GAIA the way the shortcuts do.
!define MUI_FINISHPAGE_RUN
!define MUI_FINISHPAGE_RUN_FUNCTION LaunchGaia
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
; ReadRegStr yields an EMPTY string with the error flag set for a PATH longer
; than ${NSIS_MAX_STRLEN}, indistinguishable from "not set" -- so a naive
; read-append-write REPLACES a long PATH. Every caller refuses what it cannot
; read whole, and EnumRegValue is what tells the two cases apart.
;
; Emitted twice via macros: an uninstall section can only call "un.<name>".

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
  ;
  ; Matched as ";<dir>;" inside ";<PATH>;" so ...\GAIA Terminal Hub-old never
  ; counts as ...\GAIA Terminal Hub.
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

; Windows refuses write access to a running image, so `File` over a live
; gaia-tui.exe fails mid-extraction and leaves a half-written install -- and
; under /S there is no dialog to notice it. Opening for append is the same test
; the extractor would make, one step before anything has been changed.
Function AbortIfRunning
  ; in: $R8 = file name inside $INSTDIR
retry:
  ${IfNot} ${FileExists} "$INSTDIR\$R8"
    Return
  ${EndIf}
  ClearErrors
  FileOpen $R9 "$INSTDIR\$R8" a
  ${IfNot} ${Errors}
    FileClose $R9
    Return
  ${EndIf}
  DetailPrint "$R8 is running - cannot replace it."
  ; /SD IDCANCEL: a silent install must fail rather than block on an invisible
  ; dialog, and must fail HERE, with the previous install still intact.
  MessageBox MB_RETRYCANCEL|MB_ICONEXCLAMATION \
    "GAIA is still running, so Setup cannot replace $R8.$\r$\n$\r$\nClose GAIA - quit gaia-tui, and end any gaia-agent it left behind - then click Retry.$\r$\n$\r$\nNothing has been changed; your existing installation is intact." \
    /SD IDCANCEL IDRETRY retry
  SetErrorLevel 2
  Abort "GAIA is running. Close it and run Setup again - nothing was changed."
FunctionEnd

Section "GAIA Terminal Hub" SecMain
  SectionIn RO

  StrCpy $R8 "${TUI_EXE}"
  Call AbortIfRunning
  StrCpy $R8 "${AGENT_EXE}"
  Call AbortIfRunning

  SetOutPath "$INSTDIR"

  File "${PAYLOAD_DIR}\${TUI_EXE}"
  File "${PAYLOAD_DIR}\${AGENT_EXE}"
  File "${PAYLOAD_DIR}\LICENSE.md"
  File "/oname=gaia.ico" "${ICON}"
  File "${FONTS_DIR}\${FONT_LICENSE}"

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

  Call AddToUserPath
SectionEnd

; ─── Terminal profile: font, Windows Terminal fragment, shortcuts ──────────

; Per-user font install: no admin, nothing under C:\Windows\Fonts. A face the
; user already has (per-user or machine-wide) is left alone and not recorded,
; so the uninstaller never removes it.
Function ShouldInstallFont
  ; in : $R8 = file name, $R9 = registry value name ("<full name> (TrueType)")
  ; out: $R0 = 1 install it, 0 leave it
  ; clobbers $R1
  ClearErrors
  ReadRegStr $R1 HKCU "${GAIA_FONTS_KEY}" "$R8"
  ${IfNot} ${Errors}
    ; Ours from an earlier install. Windows Terminal holds an open font file, and
    ; the pinned bytes are identical, so an in-use copy is kept rather than
    ; failing the upgrade on it.
    ${If} ${FileExists} "${USER_FONTS_DIR}\$R8"
      ClearErrors
      FileOpen $R1 "${USER_FONTS_DIR}\$R8" a
      ${If} ${Errors}
        DetailPrint "$R8 is in use - keeping the copy this setup installed earlier."
        StrCpy $R0 0
        Return
      ${EndIf}
      FileClose $R1
    ${EndIf}
    StrCpy $R0 1
    Return
  ${EndIf}
  StrCpy $R0 0
  ClearErrors
  ReadRegStr $R1 HKCU "${FONTS_REG}" "$R9"
  ${IfNot} ${Errors}
    DetailPrint "$R9 is already installed for this user - leaving it as it is."
    Return
  ${EndIf}
  ClearErrors
  ReadRegStr $R1 HKLM "${FONTS_REG}" "$R9"
  ${IfNot} ${Errors}
    DetailPrint "$R9 is already installed for all users - leaving it as it is."
    Return
  ${EndIf}
  ${If} ${FileExists} "$FONTS\$R8"
    DetailPrint "$R8 is already in the Windows fonts folder - leaving it as it is."
    Return
  ${EndIf}
  ; In the per-user folder but registered nowhere: no font Windows offers, so
  ; it is replaced and registered rather than left as an unusable stray.
  ${If} ${FileExists} "${USER_FONTS_DIR}\$R8"
    ClearErrors
    FileOpen $R1 "${USER_FONTS_DIR}\$R8" a
    ${If} ${Errors}
      DetailPrint "$R8 is in your fonts folder but unregistered and in use - close Windows Terminal and run Setup again to install it."
      Return
    ${EndIf}
    FileClose $R1
  ${EndIf}
  StrCpy $R0 1
FunctionEnd

!macro InstallFontFace FILE FULL_NAME
  StrCpy $R8 "${FILE}"
  StrCpy $R9 "${FULL_NAME} (TrueType)"
  Call ShouldInstallFont
  ${If} $R0 == 1
    SetOutPath "${USER_FONTS_DIR}"
    File "${FONTS_DIR}\${FILE}"
    WriteRegStr HKCU "${FONTS_REG}" "${FULL_NAME} (TrueType)" "${USER_FONTS_DIR}\${FILE}"
    WriteRegStr HKCU "${GAIA_FONTS_KEY}" "${FILE}" "${FULL_NAME} (TrueType)"
    ; Makes the face usable now rather than after the next sign-in.
    System::Call 'gdi32::AddFontResourceW(w "${USER_FONTS_DIR}\${FILE}") i .r0'
    ${If} $0 == 0
      DetailPrint "Installed font ${FULL_NAME} for this user; it becomes available after you next sign in."
    ${Else}
      DetailPrint "Installed font ${FULL_NAME} for this user."
    ${EndIf}
  ${EndIf}
!macroend

; Writes $R3 to the open handle $R1 as UTF-8. FileWrite would write the ANSI
; code page, and Windows Terminal rejects a fragment that is not UTF-8 -- which
; any non-ASCII user name in the install path would produce.
Function WriteUtf8
  ClearErrors
  System::Call 'kernel32::WideCharToMultiByte(i 65001, i 0, w R3, i -1, p 0, i 0, p 0, p 0) i .r5'
  ${If} $5 <= 0
    SetErrors
    Return
  ${EndIf}
  System::Alloc $5
  Pop $6
  System::Call 'kernel32::WideCharToMultiByte(i 65001, i 0, w R3, i -1, p r6, i r5, p 0, p 0) i .r5'
  ; Drop the terminating NUL the -1 length counted.
  IntOp $5 $5 - 1
  System::Call 'kernel32::WriteFile(p R1, p r6, i r5, *i .r7, p 0) i .r8'
  System::Free $6
  ${If} $8 == 0
  ${OrIf} $7 != $5
    SetErrors
  ${EndIf}
FunctionEnd

; out: error flag set when the fragment could not be written whole.
Function WriteTerminalFragment
  ; A JSON string needs every backslash in the path doubled.
  ${WordReplace} "$INSTDIR" "\" "\\" "+" $R2
  SetOutPath "$PLUGINSDIR"
  File "/oname=wt-fragment.json" "${__FILEDIR__}\wt-fragment.json"
  CreateDirectory "${WT_FRAGMENT_DIR}"
  ClearErrors
  FileOpen $R0 "$PLUGINSDIR\wt-fragment.json" r
  ${If} ${Errors}
    Return
  ${EndIf}
  FileOpen $R1 "${WT_FRAGMENT_DIR}\${WT_FRAGMENT_FILE}" w
  ${If} ${Errors}
    FileClose $R0
    SetErrors
    Return
  ${EndIf}
  StrCpy $R4 0
  ${Do}
    ClearErrors
    FileRead $R0 $R3
    ${If} ${Errors}
      ${Break}
    ${EndIf}
    ${WordReplace} "$R3" "__GAIA_INSTDIR__" "$R2" "+" $R3
    ; WordReplace flags "no match", which is every line but two.
    ClearErrors
    Call WriteUtf8
    ${If} ${Errors}
      StrCpy $R4 1
      ${Break}
    ${EndIf}
  ${Loop}
  FileClose $R0
  FileClose $R1
  ClearErrors
  ${If} $R4 == 1
    Delete "${WT_FRAGMENT_DIR}\${WT_FRAGMENT_FILE}"
    SetErrors
  ${EndIf}
FunctionEnd

; out: $R0 = path to wt.exe, or "" when Windows Terminal is not installed
Function FindWindowsTerminal
  ; The Store build exposes wt.exe as an app execution alias here.
  StrCpy $R0 "$LOCALAPPDATA\Microsoft\WindowsApps\wt.exe"
  ${If} ${FileExists} "$R0"
    Return
  ${EndIf}
  SearchPath $R0 "wt.exe"
FunctionEnd

Function LaunchGaia
  Exec '"$LaunchExe" $LaunchArgs'
FunctionEnd

Section "-Terminal profile" SecTerminalProfile
  !insertmacro InstallFontFace "IBMPlexMono-Regular.ttf"    "IBM Plex Mono"
  !insertmacro InstallFontFace "IBMPlexMono-Bold.ttf"       "IBM Plex Mono Bold"
  !insertmacro InstallFontFace "IBMPlexMono-Italic.ttf"     "IBM Plex Mono Italic"
  !insertmacro InstallFontFace "IBMPlexMono-BoldItalic.ttf" "IBM Plex Mono Bold Italic"
  SendMessage ${HWND_BROADCAST} ${WM_FONTCHANGE} 0 0 /TIMEOUT=5000
  SetOutPath "$INSTDIR"

  ; Written even without Windows Terminal: it is inert until WT is installed,
  ; and then the GAIA profile simply appears.
  StrCpy $R5 1
  Call WriteTerminalFragment
  ${If} ${Errors}
    StrCpy $R5 0
  ${EndIf}
  ; Shortcuts take $OUTDIR as their start-in folder, and the function left it on $PLUGINSDIR.
  SetOutPath "$INSTDIR"
  ${If} $R5 == 0
    DetailPrint "Could not write the Windows Terminal profile to ${WT_FRAGMENT_DIR}\${WT_FRAGMENT_FILE}."
    ; /SD IDOK, or a silent install blocks forever on a dialog nobody can see.
    MessageBox MB_OK|MB_ICONEXCLAMATION \
      "GAIA is installed, but Setup could not write its Windows Terminal profile to:$\r$\n$\r$\n${WT_FRAGMENT_DIR}\${WT_FRAGMENT_FILE}$\r$\n$\r$\nThe shortcuts will run gaia-tui in the default console instead. Check that folder is writable, then run Setup again." \
      /SD IDOK
  ${Else}
    DetailPrint "Added the GAIA profile to Windows Terminal (${WT_FRAGMENT_DIR}\${WT_FRAGMENT_FILE})."
  ${EndIf}

  ; ── Shortcuts ──
  ; cobra's Explorer guard is disabled in gaia-tui (tui/internal/cli/root.go);
  ; left at its default, a shortcut that runs it directly would print "This is
  ; a command line tool" and exit, because a shortcut launches via Explorer.
  Call FindWindowsTerminal
  ${If} $R0 != ""
  ${AndIf} $R5 == 1
    StrCpy $LaunchExe "$R0"
    StrCpy $LaunchArgs '-p "${WT_PROFILE_GUID}"'
    DetailPrint "Shortcuts open GAIA in Windows Terminal with the GAIA profile."
  ${Else}
    StrCpy $LaunchExe "$INSTDIR\${TUI_EXE}"
    StrCpy $LaunchArgs ""
    ${If} $R0 == ""
      ; Explicit, not a fallback: the console host cannot load a WT profile.
      DetailPrint "Windows Terminal is not installed - shortcuts run gaia-tui in the default console, without the GAIA font and colours. Install Windows Terminal and re-run Setup to use the GAIA profile."
    ${Else}
      DetailPrint "Shortcuts run gaia-tui in the default console because the Windows Terminal profile could not be written."
    ${EndIf}
  ${EndIf}
  CreateDirectory "$SMPROGRAMS\GAIA"
  CreateShortcut "$SMPROGRAMS\GAIA\${PRODUCT_NAME}.lnk" "$LaunchExe" "$LaunchArgs" "$INSTDIR\gaia.ico" 0
  CreateShortcut "$SMPROGRAMS\GAIA\Uninstall ${PRODUCT_NAME}.lnk" "$INSTDIR\Uninstall.exe"
  CreateShortcut "$DESKTOP\${PRODUCT_NAME}.lnk" "$LaunchExe" "$LaunchArgs" "$INSTDIR\gaia.ico" 0
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

; Removes a face only when this setup recorded installing it.
!macro UninstallFontFace FILE FULL_NAME
  ClearErrors
  ReadRegStr $R0 HKCU "${GAIA_FONTS_KEY}" "${FILE}"
  ${IfNot} ${Errors}
    System::Call 'gdi32::RemoveFontResourceW(w "${USER_FONTS_DIR}\${FILE}") i .r0'
    Delete "${USER_FONTS_DIR}\${FILE}"
    ; Registration goes only with the file, so a face still in use stays a
    ; working installed font rather than an unregistered stray.
    ${If} ${FileExists} "${USER_FONTS_DIR}\${FILE}"
      DetailPrint "${FULL_NAME} is still installed - Windows Terminal has it open. Close Windows Terminal, then remove it from Settings > Personalization > Fonts."
    ${Else}
      DeleteRegValue HKCU "${FONTS_REG}" "${FULL_NAME} (TrueType)"
      DetailPrint "Removed font ${FULL_NAME}."
    ${EndIf}
  ${EndIf}
!macroend

Section "Uninstall"
  Delete "$INSTDIR\${TUI_EXE}"
  Delete "$INSTDIR\${AGENT_EXE}"
  Delete "$INSTDIR\LICENSE.md"
  Delete "$INSTDIR\gaia.ico"
  Delete "$INSTDIR\${FONT_LICENSE}"

  ; Before the GAIA key is deleted: it records which faces are ours to remove.
  !insertmacro UninstallFontFace "IBMPlexMono-Regular.ttf"    "IBM Plex Mono"
  !insertmacro UninstallFontFace "IBMPlexMono-Bold.ttf"       "IBM Plex Mono Bold"
  !insertmacro UninstallFontFace "IBMPlexMono-Italic.ttf"     "IBM Plex Mono Italic"
  !insertmacro UninstallFontFace "IBMPlexMono-BoldItalic.ttf" "IBM Plex Mono Bold Italic"
  SendMessage ${HWND_BROADCAST} ${WM_FONTCHANGE} 0 0 /TIMEOUT=5000

  Delete "${WT_FRAGMENT_DIR}\${WT_FRAGMENT_FILE}"
  RMDir  "${WT_FRAGMENT_DIR}"

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

  ; Optional cleanup deletes the entire shared GAIA home, including other
  ; installations' runtimes. Interactive and silent defaults preserve it.
  MessageBox MB_YESNO|MB_ICONQUESTION|MB_DEFBUTTON2 \
    "Delete ALL files in $PROFILE\.gaia?$\r$\n$\r$\nThis includes chats, documents, custom agents, skills, connector sign-ins and permissions, memory, MCP server settings, config, logs, and shared Python and terminal runtimes.$\r$\n$\r$\nOther GAIA installations using this folder will stop working. This cannot be undone. Choose No to keep everything." \
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
