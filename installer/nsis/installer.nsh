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

!include "LogicLib.nsh"

!ifndef BUILD_UNINSTALLER
  Var GaiaRunValue
!endif

; On an update electron-builder runs the old uninstaller, customUnInstall
; included, before customInstall. Read here so a user's Run value survives that.
!macro customInit
  ReadRegStr $GaiaRunValue HKCU "Software\Microsoft\Windows\CurrentVersion\Run" \
      "${PRODUCT_NAME}"
  ${If} $GaiaRunValue == ""
    ReadRegStr $GaiaRunValue HKCU "Software\Microsoft\Windows\CurrentVersion\Run" \
        "GAIA Agent UI"
  ${EndIf}
!macroend

!macro customInstall
  ; Clean up the old autostart key from pre-rename installs so users
  ; who upgrade from "GAIA Agent UI" don't end up with an orphaned
  ; Run entry pointing at a binary path that no longer exists.
  DeleteRegValue HKCU "Software\Microsoft\Windows\CurrentVersion\Run" "GAIA Agent UI"

  ${If} $GaiaRunValue != ""
    WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Run" \
        "${PRODUCT_NAME}" "$GaiaRunValue"
  ${EndIf}
!macroend

; ─── Uninstall: clean up the autostart Run key ─────────────────────────
;
; A system Lemonade Server from an earlier release is intentionally NOT
; uninstalled: `gaia uninstall --purge-lemonade` removes it.

!macro customUnInstall
  DeleteRegValue HKCU "Software\Microsoft\Windows\CurrentVersion\Run" \
      "${PRODUCT_NAME}"
  ; Also clean up the old pre-rename autostart key in case the user
  ; is uninstalling without ever having upgraded through the rename.
  DeleteRegValue HKCU "Software\Microsoft\Windows\CurrentVersion\Run" \
      "GAIA Agent UI"

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
