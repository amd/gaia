// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * Run the app's async cleanup exactly once on quit, and never let it hold
 * the quit forever.
 *
 * Electron does not await before-quit/will-quit handlers, so will-quit
 * cancels the quit, runs the cleanup, and quits again. A second quit while
 * the cleanup runs (tray Quit then Cmd+Q, or a fatal error mid-quit) must
 * wait for that same cleanup: starting another one let the second finish
 * first and end the app while the first was still stopping the backend.
 */

"use strict";

/**
 * Longest the quit waits for cleanup before going ahead without it (ms).
 * Covers the slowest agent stop: two 20 s Windows process-table reads, the
 * agent's shutdown wait and the kill grace.
 */
const CLEANUP_TIMEOUT_MS = 50000;

/**
 * @param {object} deps
 * @param {Electron.App} deps.app
 * @param {() => Promise<void>} deps.cleanup
 * @param {number} [deps.timeoutMs]
 * @param {{log: Function, error: Function}} [deps.logger]
 * @returns {{ readonly done: boolean, readonly running: Promise<void>|null }}
 */
function installQuitCleanup({ app, cleanup, timeoutMs = CLEANUP_TIMEOUT_MS, logger = console }) {
  let done = false;
  let running = null;

  app.on("will-quit", (event) => {
    if (done) return;
    event.preventDefault();
    if (running) {
      logger.log("[main] will-quit: cleanup already running; quitting when it finishes");
      return;
    }
    logger.log("[main] will-quit: performing async cleanup...");

    let timer = null;
    const deadline = new Promise((resolve) => {
      timer = setTimeout(() => {
        logger.error(
          `[main] Cleanup did not finish within ${timeoutMs} ms; quitting anyway. ` +
            "A backend or agent process may still be running — check Task Manager or `ps`."
        );
        resolve();
      }, timeoutMs);
    });

    running = Promise.race([
      Promise.resolve()
        .then(cleanup)
        .catch((err) => {
          logger.error("[main] Cleanup error:", (err && err.stack) || err);
        }),
      deadline,
    ])
      // Re-quit on a later task: Electron drops a quit() that arrives while it
      // is still unwinding the will-quit it just prevented, which is what an
      // instant cleanup (no backend left to stop) used to hit.
      .then(() => new Promise((resolve) => setImmediate(resolve)))
      .then(() => {
        clearTimeout(timer);
        done = true;
        logger.log("[main] Cleanup complete, quitting...");
        app.quit();
      });
  });

  return {
    get done() {
      return done;
    },
    get running() {
      return running;
    },
  };
}

module.exports = { installQuitCleanup, CLEANUP_TIMEOUT_MS };
