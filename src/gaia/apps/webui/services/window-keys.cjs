// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * Keys the default app menu used to carry on Windows and Linux. The window has
 * no menu there, so zoom, reload and DevTools are wired here instead.
 */

const ZOOM_STEP = 0.5;
const ZOOM_MIN = -3;
const ZOOM_MAX = 5;

/** The action a keyDown asks for, or null. `input` is Electron's before-input-event Input. */
function windowKeyAction(input, { devTools }) {
  if (input.type !== "keyDown") return null;
  const mod = input.control || input.meta;
  if (input.key === "F12") return devTools ? "devtools" : null;
  if (!mod || input.alt) return null;
  const key = input.key.toLowerCase();
  if (input.shift && key === "i") return devTools ? "devtools" : null;
  if (key === "=" || key === "+") return "zoom-in";
  if (key === "-" || key === "_") return "zoom-out";
  if (key === "0") return "zoom-reset";
  if (key === "r" && !input.shift) return "reload";
  return null;
}

/** Wire the keys onto a window's webContents. */
function installWindowKeys(webContents, { devTools }) {
  webContents.on("before-input-event", (event, input) => {
    const action = windowKeyAction(input, { devTools });
    if (!action) return;
    event.preventDefault();
    const level = webContents.getZoomLevel();
    if (action === "zoom-in") webContents.setZoomLevel(Math.min(ZOOM_MAX, level + ZOOM_STEP));
    else if (action === "zoom-out") webContents.setZoomLevel(Math.max(ZOOM_MIN, level - ZOOM_STEP));
    else if (action === "zoom-reset") webContents.setZoomLevel(0);
    else if (action === "reload") webContents.reload();
    else if (action === "devtools") webContents.toggleDevTools();
  });
}

module.exports = { windowKeyAction, installWindowKeys, ZOOM_STEP, ZOOM_MIN, ZOOM_MAX };
