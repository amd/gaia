// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * When to first show a window created with `show: false`.
 *
 * Electron's "ready-to-show" waits for the first painted frame. A Wayland
 * compositor asks for no frames from a window that isn't on screen, so there
 * the event never comes and the window would stay hidden for good.
 */

/** How long after the page loads to stop waiting for "ready-to-show". */
const PAINT_WAIT_MS = 1000;

/**
 * Call `reveal` once: on "ready-to-show", or `waitMs` after the page finished
 * loading when that event hasn't come.
 *
 * @param {Electron.BrowserWindow} win
 * @param {(via: "ready-to-show" | "page-loaded") => void} reveal
 * @param {{waitMs?: number, setTimer?: typeof setTimeout, clearTimer?: typeof clearTimeout}} [deps]
 */
function revealWhenReady(
  win,
  reveal,
  { waitMs = PAINT_WAIT_MS, setTimer = setTimeout, clearTimer = clearTimeout } = {}
) {
  let done = false;
  let timer = null;
  const run = (via) => {
    if (done) return;
    done = true;
    if (timer !== null) clearTimer(timer);
    reveal(via);
  };
  win.once("ready-to-show", () => run("ready-to-show"));
  win.webContents.once("did-finish-load", () => {
    if (!done) timer = setTimer(() => run("page-loaded"), waitMs);
  });
}

module.exports = { revealWhenReady, PAINT_WAIT_MS };
