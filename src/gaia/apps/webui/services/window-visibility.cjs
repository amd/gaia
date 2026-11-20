// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * Tell the GAIA service when the app goes out of sight: every window in the
 * tray, minimised or closed. GAIA's daemon gives the models' memory back once
 * the app has sat like that, unused, for `hidden_idle_unload_minutes`.
 *
 * Read from the windows themselves, not the page: Chromium marks a page hidden
 * whenever another window covers it, and GAIA behind a browser is still in use.
 */

const { backendJson } = require("./quit-guard.cjs");

const ROUTE = "/api/system/window-visibility";
// A state the service has not heard yet is sent again this often.
const RESEND_MS = 5000;
const WINDOW_EVENTS = ["show", "hide", "minimize", "restore", "closed"];

/** No window is on screen: each is in the tray, minimised or gone. */
function appHidden(windows) {
  return windows.every((w) => w.isDestroyed() || !w.isVisible() || w.isMinimized());
}

/**
 * @param {object} opts
 * @param {() => number | null} opts.getPort The GAIA service's port, or null while it is down.
 * @param {() => Electron.BrowserWindow[]} opts.getWindows The app's own windows.
 * @param {() => boolean} [opts.quitting] True once a quit is under way: nothing is sent.
 */
function createWindowVisibility({
  getPort,
  getWindows,
  quitting = () => false,
  send = backendJson,
  log = console,
  setTimer = setTimeout,
  clearTimer = clearTimeout,
}) {
  let heard = null; // what the service last acknowledged
  let lastError = null;
  let timer = null;
  let queue = Promise.resolve();

  async function sync() {
    if (timer) {
      clearTimer(timer);
      timer = null;
    }
    if (quitting()) return;
    const hidden = appHidden(getWindows());
    if (hidden === heard) return;
    const port = getPort();
    if (!port) return; // backendStarted() sends it once the service is up
    try {
      await send(port, ROUTE, { method: "POST", body: { hidden } });
      heard = hidden;
      lastError = null;
      log.log(`[window-visibility] told the GAIA service the app is ${hidden ? "hidden" : "showing"}`);
    } catch (err) {
      // Not once per attempt: a service that is down fails every five seconds.
      if (err.message !== lastError) {
        log.error(
          `[window-visibility] could not tell the GAIA service the app is ` +
            `${hidden ? "hidden" : "showing"}: ${err.message}. Trying again every ${RESEND_MS / 1000} s; ` +
            "until it hears, GAIA may unload its models at the wrong time."
        );
      }
      lastError = err.message;
      timer = setTimer(report, RESEND_MS);
      if (timer && typeof timer.unref === "function") timer.unref();
    }
  }

  // One at a time, in order: a slow "hidden" must not land after a fast "showing".
  function report() {
    queue = queue.then(sync, sync);
    return queue;
  }

  return {
    /** Follow one of the app's windows. */
    watch(win) {
      for (const event of WINDOW_EVENTS) win.on(event, report);
    },
    /** The service (re)started and knows nothing of the window: say it again. */
    backendStarted() {
      heard = null;
      return report();
    },
    report,
  };
}

module.exports = { ROUTE, RESEND_MS, WINDOW_EVENTS, appHidden, createWindowVisibility };
