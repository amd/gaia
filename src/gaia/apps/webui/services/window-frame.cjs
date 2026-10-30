// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * How the main window's frame is drawn on each platform. Pure so the
 * BrowserWindow options are testable without launching Electron.
 *
 * - "mac": no title bar; the traffic lights sit over the app's top row.
 * - "overlay" (Windows): no title bar; Chromium draws the controls over it.
 * - "native" (Linux): the system frame. GNOME Wayland has no server-side
 *   decorations, so a hidden title bar there loses its controls.
 */

// Keep in sync with --titlebar-h in src/styles/index.css.
const TITLE_BAR_HEIGHT = 48;
// Until the renderer sends its theme colors, the controls use the dark canvas.
const TITLE_BAR_INITIAL = { color: "#1A1917", symbolColor: "#EFECE6" };

/** @returns {"mac" | "overlay" | "native"} */
function titleBarKind(platform) {
  if (platform === "darwin") return "mac";
  if (platform === "win32") return "overlay";
  return "native";
}

/** BrowserWindow options that set up the frame, spread into the constructor. */
function titleBarOptions(platform) {
  switch (titleBarKind(platform)) {
    case "mac":
      return { titleBarStyle: "hidden", trafficLightPosition: { x: 16, y: 16 } };
    case "overlay":
      return {
        titleBarStyle: "hidden",
        titleBarOverlay: { ...TITLE_BAR_INITIAL, height: TITLE_BAR_HEIGHT },
      };
    default:
      return {};
  }
}

module.exports = { TITLE_BAR_HEIGHT, titleBarKind, titleBarOptions };
