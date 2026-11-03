; Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
; SPDX-License-Identifier: MIT
;
; GAIA Agent UI — custom NSIS installer additions.
;
; This file is `include`d by the electron-builder-generated NSIS script.
; It writes the Windows autostart registry entry so GAIA Agent UI starts
; on user login (default ON, matching Slack/Discord/Zoom convention per
; plan §4.1), and cleans up that registry entry on uninstall.
;
; ─── Why "always on" instead of a finish-page checkbox ──────────────────
;
; An earlier revision of this file used MUI_FINISHPAGE_SHOWREADME_FUNCTION
; to display a "Start GAIA when I sign in to Windows" checkbox on the
; finish page. That approach is incompatible with electron-builder's
; standard page flow: electron-builder emits BOTH MUI_PAGE_FINISH and
; MUI_UNPAGE_FINISH, and the finish-page macro is shared between them.
; If MUI_FINISHPAGE_SHOWREADME_FUNCTION points at a globally-named
; function (e.g. `GaiaSetAutoStart`), the uninstaller's finish page tries
; to `Call GaiaSetAutoStart` — but NSIS requires all functions called
; from the uninstall section to be prefixed with `un.`. The build fails
; with:
;
;   Call must be used with function names starting with "un." in the uninstall section.
;
; Fixing this by defining a parallel `un.GaiaSetAutoStart` doesn't work
; either — NSIS prohibits using `!undef` on MUI macro-reserved symbols
; between the two page insertions when they're both emitted by a parent
; script we don't control. The simplest robust fix is to drop the
; checkbox entirely and write the autostart key unconditionally. This
; matches the plan's "default ON" intent and mirrors how Slack/Discord
; behave. Users who want to disable autostart can:
;
;   1. Use Settings → General → "Start GAIA when you sign in". That switch
;      owns this same Run value (WIN_LOGIN_ITEM_NAME in
;      services/tray-manager.cjs must equal ${PRODUCT_NAME}, args included)
;      and removes it again at startup if an update wrote it back.
;   2. Or disable via Windows Task Manager → Startup tab
;   3. Or delete the HKCU Run key manually with regedit
;
; electron-builder exposes extension macros we hook into:
;
;   customInstall    — runs at the end of the main install section
;   customUnInstall  — runs at the start of the uninstall section
;
; Reference: https://github.com/electron-userland/electron-builder

; ─── Install: write the autostart Run key ──────────────────────────────
;
; HKCU\Software\Microsoft\Windows\CurrentVersion\Run is the per-user
; autostart registry hive. We pass `--minimized` so the Electron app
; starts into the tray (handled by services/tray-manager.cjs) rather
; than popping a window on login.
;
; Using HKCU (not HKLM) matches perMachine=false in electron-builder.yml
; — we're a per-user install, so per-user autostart is correct.

!macro customInstall
  ; Clean up the old autostart key from pre-rename installs so users
  ; who upgrade from "GAIA Agent UI" don't end up with an orphaned
  ; Run entry pointing at a binary path that no longer exists.
  DeleteRegValue HKCU "Software\Microsoft\Windows\CurrentVersion\Run" "GAIA Agent UI"

  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Run" \
      "${PRODUCT_NAME}" \
      '"$INSTDIR\${PRODUCT_FILENAME}.exe" --minimized'
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
