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
;   1. Use the GAIA Agent UI tray menu → Settings → "Launch at login"
;      (see services/tray-manager.cjs)
;   2. Or disable via Windows Task Manager → Startup tab
;   3. Or delete the HKCU Run key manually with regedit
;
; electron-builder exposes extension macros we hook into:
;
;   customInstall    — runs at the end of the main install section
;   customUnInstall  — runs at the start of the uninstall section
;
; Reference: https://www.electron.build/configuration/nsis.html#custom-nsis-script

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
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Run" \
      "${PRODUCT_NAME}" \
      '"$INSTDIR\${PRODUCT_FILENAME}.exe" --minimized'
!macroend

; ─── Uninstall: clean up the autostart Run key ─────────────────────────

!macro customUnInstall
  DeleteRegValue HKCU "Software\Microsoft\Windows\CurrentVersion\Run" \
      "${PRODUCT_NAME}"
!macroend
