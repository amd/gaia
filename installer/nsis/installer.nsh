; Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
; SPDX-License-Identifier: MIT
;
; GAIA Agent UI — custom NSIS installer additions.
;
; This file is `include`d by the electron-builder-generated NSIS script.
; It adds a "Start GAIA when I sign in to Windows" checkbox to the finish
; page (default ON, matching Slack/Discord/Zoom convention per plan §4.1)
; and wires the corresponding uninstall cleanup for the autostart registry
; entry.
;
; electron-builder exposes three extension macros we can hook into:
;
;   customInstall             — runs at the end of the main install section
;   customUnInstall           — runs at the start of the uninstall section
;   customFinishPage (older)  — NOT used; we rely on MUI_FINISHPAGE_SHOWREADME
;
; Reference: https://www.electron.build/configuration/nsis.html#custom-nsis-script
; Phase H will extend this file with SignPath integration.
; Phase E may replace the banner BMPs but should not need to touch this file.

; ─── Autostart checkbox on finish page ─────────────────────────────────
;
; electron-builder already defines the MUI finish page. We repurpose the
; "show readme" hook to display our own checkbox label and callback. The
; checkbox is checked by default (matches the "default ON" convention).
;
; NOTE: these !defines must be set BEFORE electron-builder emits the
; !insertmacro MUI_PAGE_FINISH line. electron-builder processes includes
; at the top of the generated .nsi, so defines here are picked up in
; time.

!define MUI_FINISHPAGE_SHOWREADME ""
!define MUI_FINISHPAGE_SHOWREADME_TEXT "Start ${PRODUCT_NAME} when I sign in to Windows"
!define MUI_FINISHPAGE_SHOWREADME_FUNCTION GaiaSetAutoStart

Function GaiaSetAutoStart
  ; Write HKCU\...\Run so GAIA Agent UI starts on login for the current
  ; user. Passing --minimized asks the Electron app to start into the
  ; tray (handled by services/tray-manager.cjs).
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Run" \
      "${PRODUCT_NAME}" \
      '"$INSTDIR\${PRODUCT_FILENAME}.exe" --minimized'
FunctionEnd

; ─── Uninstall cleanup ─────────────────────────────────────────────────
;
; electron-builder's `customUnInstall` macro runs as part of the main
; uninstaller section. Clean up the autostart Run key we may have written.

!macro customUnInstall
  DeleteRegValue HKCU "Software\Microsoft\Windows\CurrentVersion\Run" \
      "${PRODUCT_NAME}"
!macroend
