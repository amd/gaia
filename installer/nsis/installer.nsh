; Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
; SPDX-License-Identifier: MIT
;
; GAIA Agent UI — custom NSIS installer additions.
;
; This file is `include`d by the electron-builder-generated NSIS script.
;
; Start at sign-in is the app's: Settings > General ("Start GAIA when you sign
; in", services/tray-manager.cjs) owns the HKCU Run value named ${PRODUCT_NAME},
; which must equal WIN_LOGIN_ITEM_NAME there. The installer never turns it on.

; At file scope, not in a macro: electron-builder may expand a macro twice.
!include "LogicLib.nsh"

!ifndef BUILD_UNINSTALLER
  Var GaiaRunValue
  Var GaiaRunLegacy
!endif

; On an update electron-builder runs the old uninstaller, customUnInstall
; included, before customInstall. Read here so a user's Run value survives that.
!macro customInit
  ReadRegStr $GaiaRunValue HKCU "Software\Microsoft\Windows\CurrentVersion\Run" \
      "${PRODUCT_NAME}"
  ReadRegStr $GaiaRunLegacy HKCU "Software\Microsoft\Windows\CurrentVersion\Run" \
      "GAIA Agent UI"
!macroend

!macro customInstall
  ; Clean up the old autostart key from pre-rename installs so users
  ; who upgrade from "GAIA Agent UI" don't end up with an orphaned
  ; Run entry pointing at a binary path that no longer exists.
  DeleteRegValue HKCU "Software\Microsoft\Windows\CurrentVersion\Run" "GAIA Agent UI"

  ; The old value points at the old binary, so only the choice carries over.
  ${If} $GaiaRunValue == ""
  ${AndIf} $GaiaRunLegacy != ""
    StrCpy $GaiaRunValue '"$INSTDIR\${PRODUCT_FILENAME}.exe" --minimized'
  ${EndIf}

  ${If} $GaiaRunValue != ""
    WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Run" \
        "${PRODUCT_NAME}" "$GaiaRunValue"
  ${EndIf}
!macroend

; ─── Uninstall: clean up the autostart Run key ─────────────────────────
;
; Lemonade Server is not uninstalled here. GAIA's own copy lives in
; ~/.gaia/lemonade, so it goes only if the user agrees to delete ~/.gaia below;
; a system Lemonade an older GAIA installer added stays because other apps may
; use it.

!macro customUnInstall
  DeleteRegValue HKCU "Software\Microsoft\Windows\CurrentVersion\Run" \
      "${PRODUCT_NAME}"
  ; Also clean up the old pre-rename autostart key in case the user
  ; is uninstalling without ever having upgraded through the rename.
  DeleteRegValue HKCU "Software\Microsoft\Windows\CurrentVersion\Run" \
      "GAIA Agent UI"

  ; The daemon and its agents have no window, so they outlive the app unless
  ; stopped here. A failure is logged, not fatal: the uninstall still finishes.
  Push $0
  ${If} ${FileExists} "$PROFILE\.gaia\venv\Scripts\gaia.exe"
    DetailPrint "Stopping the GAIA daemon and its agents"
    ; gaia prints emoji, which the captured pipe's code page can't encode.
    System::Call 'Kernel32::SetEnvironmentVariable(t "PYTHONIOENCODING", t "utf-8")i'
    nsExec::ExecToLog /TIMEOUT=120000 '"$PROFILE\.gaia\venv\Scripts\gaia.exe" daemon stop'
    Pop $0
    ; 3: the daemon stopped; only GAIA's Lemonade Server did not.
    ${If} $0 == 3
      DetailPrint "The GAIA daemon stopped, but GAIA's Lemonade Server did not. The lines above say how to stop it."
    ${ElseIf} $0 != 0
      DetailPrint "Could not stop the GAIA daemon (gaia daemon stop returned $0). It keeps running until you restart your PC."
    ${EndIf}
  ${Else}
    DetailPrint "No GAIA backend at $PROFILE\.gaia\venv, so no GAIA daemon to stop"
  ${EndIf}
  Pop $0

  ; Optional cleanup deletes the entire shared GAIA home, including other
  ; installations' runtimes. Interactive and silent defaults preserve it.
  ;
  ; Uses a relative jump (IDNO +2) instead of named labels to avoid
  ; label-collision risk if electron-builder ever expands this macro
  ; more than once.
  MessageBox MB_YESNO|MB_ICONQUESTION|MB_DEFBUTTON2 \
      "Delete ALL files in $PROFILE\.gaia?$\r$\n$\r$\nThis includes chats, documents, custom agents, skills, connector sign-ins and permissions, memory, MCP server settings, config, logs, and shared Python and terminal runtimes.$\r$\n$\r$\nOther GAIA installations using this folder will stop working. This cannot be undone. Choose No to keep everything." \
      /SD IDNO IDNO +2
  RmDir /r "$PROFILE\.gaia"
!macroend
