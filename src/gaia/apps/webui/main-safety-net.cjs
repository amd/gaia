// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * main-safety-net.cjs — Hardened Electron main-process error handling.
 *
 * Extracted from main.cjs so tests can require this module without triggering
 * main.cjs side effects. All Electron objects are dependency-injected.
 *
 * Fixes for issue #934 (ERR_STREAM_WRITE_AFTER_END after fresh install):
 *   - process.on('uncaughtException') catches stream 'error' events that
 *     propagate because the write stream has no listener.
 *   - process.on('unhandledRejection') catches rejected app.whenReady() chain.
 *   - installLogTee() attaches stream.on('error') so stream errors are handled
 *     before they can become uncaughtException (root-cause fix).
 *
 * A fatal error quits through app.quit(), not process.exit(): only the quit
 * path (will-quit in main.cjs) stops the backend and the agents, so exiting
 * directly left them running after the window was gone (#3498). A timer
 * still forces the exit if that cleanup hangs.
 */

"use strict";

const fs = require("fs");
const path = require("path");
const os = require("os");

// ── Counter helpers ───────────────────────────────────────────────────────────
// ~/.gaia/electron-startup-failures.json counts crashes since the last time a
// window was focused. At SAFE_MODE_THRESHOLD the next launch enters safe mode.

/** Crashes in a row, with no focused window between them, that make the next launch start in safe mode. */
const SAFE_MODE_THRESHOLD = 3;

function warn(msg) {
  try { process.stderr.write(`[safety-net] ${msg}\n`); } catch { }
}

function counterPath(homedir) {
  return path.join(homedir(), ".gaia", "electron-startup-failures.json");
}

function readCount(homedir) {
  const p = counterPath(homedir);
  let raw;
  try {
    raw = fs.readFileSync(p, "utf8");
  } catch (err) {
    if (err && err.code === "ENOENT") return 0;
    warn(`cannot read ${p}, treating the crash count as 0: ${err.message}`);
    return 0;
  }
  let count;
  try {
    count = JSON.parse(raw).count;
  } catch (err) {
    warn(`${p} is not valid JSON, treating the crash count as 0: ${err.message}`);
    return 0;
  }
  if (count === undefined) return 0;
  if (!Number.isInteger(count) || count < 0) {
    warn(`${p} has an invalid count ${JSON.stringify(count)}, treating it as 0`);
    return 0;
  }
  return count;
}

function writeCount(n, homedir) {
  const p = counterPath(homedir);
  try {
    fs.mkdirSync(path.dirname(p), { recursive: true });
    fs.writeFileSync(p, JSON.stringify({ count: n }), { encoding: "utf8" });
  } catch (err) {
    try { process.stderr.write(`[safety-net] writeCount failed: ${err.message}\n`); } catch { }
  }
}

// ── Log helper ────────────────────────────────────────────────────────────────

function appendLog(logPath, msg) {
  try {
    fs.mkdirSync(path.dirname(logPath), { recursive: true });
    fs.appendFileSync(logPath, msg + "\n", { encoding: "utf8" });
  } catch (err) {
    try { process.stderr.write(`[safety-net] log append failed: ${err.message}\n`); } catch { }
  }
}

/** How long a fatal error's quit may take before the process is ended outright (ms). */
const FATAL_EXIT_GRACE_MS = 25000;

// ── Core installer ────────────────────────────────────────────────────────────

/**
 * Install the safety-net handlers on the current process.
 *
 * @param {object} opts
 * @param {string}   opts.logPath       - Path to append FATAL lines into.
 * @param {object}   opts.dialogModule  - Electron dialog (injected for tests).
 * @param {object}   opts.appModule     - Electron app EventEmitter (injected).
 * @param {Function} [opts.homedirFn]   - Override for os.homedir (tests).
 * @param {Function} [opts.beforeQuit]  - Called before the fatal quit, e.g.
 *   so the quit guard does not ask the user to confirm it.
 * @param {number}   [opts.exitGraceMs] - Override for FATAL_EXIT_GRACE_MS.
 */
function installSafetyNet({
  logPath,
  dialogModule,
  appModule,
  homedirFn,
  beforeQuit,
  exitGraceMs = FATAL_EXIT_GRACE_MS,
}) {
  const homedir = homedirFn || (() => os.homedir());

  // Per-handler re-entry guard (closure-scoped — each installSafetyNet
  // call gets its own, intentionally; see test_main_error_handling.js).
  let _inFatalHandler = false;

  // Quit through will-quit so its cleanup stops the backend and agents; the
  // timer ends the process if that cleanup never finishes.
  function quitAfterFatal() {
    const timer = setTimeout(() => {
      appendLog(
        logPath,
        `[${new Date().toISOString()}] FATAL_EXIT cleanup did not finish within ${exitGraceMs} ms; exiting`
      );
      process.exit(1);
    }, exitGraceMs);
    if (typeof beforeQuit === "function") {
      try {
        beforeQuit();
      } catch (hookErr) {
        appendLog(logPath, `[${new Date().toISOString()}] FATAL_QUIT beforeQuit failed: ${hookErr.stack || hookErr}`);
      }
    }
    try {
      appModule.quit();
    } catch (quitErr) {
      clearTimeout(timer);
      appendLog(logPath, `[${new Date().toISOString()}] FATAL_QUIT app.quit() failed, exiting now: ${quitErr.stack || quitErr}`);
      process.exit(1);
    }
  }

  function fatal(err) {
    const stack = (err && err.stack) ? err.stack : String(err);
    const ts = new Date().toISOString();

    // A second fatal while the first is still showing its dialog or quitting
    // (an EPIPE from a child that died during cleanup, say) is logged only:
    // exiting here would skip the cleanup that is already under way.
    if (_inFatalHandler) {
      appendLog(logPath, `[${ts}] FATAL (while already quitting) ${stack}`);
      return;
    }
    _inFatalHandler = true;

    // Write to log BEFORE showing dialog so the entry survives even if
    // dialog.showErrorBox itself crashes.
    appendLog(logPath, `[${ts}] FATAL ${stack}`);

    // Increment crash-loop counter.
    writeCount(readCount(homedir) + 1, homedir);

    // Pre-app.ready on Windows, showMessageBoxSync silently no-ops;
    // showErrorBox is the only dialog that works in that window.
    // Bare catch: intentional swallow — we are already in the fatal-exit
    // path with no upstream caller to surface errors to.
    try {
      if (appModule.isReady()) {
        dialogModule.showMessageBoxSync({
          type: "error",
          title: "GAIA crashed",
          message: stack,
          buttons: ["OK"],
        });
      } else {
        dialogModule.showErrorBox("GAIA failed to start", stack);
      }
    } catch { } // intentional: fatal path, no upstream

    quitAfterFatal();
  }

  // Wire process-level handlers.
  process.on("uncaughtException", (err) => fatal(err));
  process.on("unhandledRejection", (reason) => {
    const err = reason instanceof Error ? reason : new Error(String(reason));
    fatal(err);
  });

  // Reset counter on the first successful user interaction. Resetting at
  // loadApp() is too early — the user may crash before their first focus.
  appModule.on("browser-window-focus", () => writeCount(0, homedir));

  // Renderer and GPU-process crashes don't fire uncaughtException — route
  // them through fatal() so they get the same dialog + counter treatment.
  appModule.on("render-process-gone", (_event, _webContents, details) => {
    fatal(new Error(`render-process-gone: reason=${details && details.reason}`));
  });

  appModule.on("child-process-gone", (_event, details) => {
    const reason = details && details.reason;
    const type = details && details.type;
    // Ignore expected terminations during shutdown so the crash dialog
    // doesn't flash on a clean quit.
    if (reason === "clean-exit" || reason === "killed") return;
    // A GPU-process crash is recoverable — Chromium relaunches the GPU process
    // and falls back to software rendering. It fires routinely in GPU-less
    // environments (Windows Sandbox, VMs, RDP), so log it and keep running
    // instead of killing an otherwise-healthy app.
    if (type === "GPU") {
      appendLog(
        logPath,
        `[${new Date().toISOString()}] GPU_PROCESS_GONE reason=${reason}`
      );
      return;
    }
    fatal(new Error(`child-process-gone: type=${type} reason=${reason}`));
  });

  return { fatal };
}

// ── Safe mode ─────────────────────────────────────────────────────────────────

const SAFE_MODE_BUTTONS = ["Reset state", "Open log folder", "Quit"];
const SAFE_MODE_RESET = 0;
const SAFE_MODE_OPEN_LOGS = 1;
const SAFE_MODE_QUIT = 2;

/**
 * Ask a user whose last launches crashed what to do, before any other start-up
 * work runs. Must be called after app ready: before it, showMessageBoxSync
 * does nothing on Windows.
 *
 * Reset state deletes the crash counter and relaunches; Open log folder opens
 * ~/.gaia and asks again; Quit exits. If the dialog itself fails, GAIA exits
 * with code 1 rather than retry the start-up that kept crashing.
 *
 * @param {object} opts
 * @param {number}   opts.count         - The crash count read at launch.
 * @param {object}   opts.dialogModule  - Electron dialog (injected for tests).
 * @param {object}   opts.appModule     - Electron app (injected for tests).
 * @param {object}   opts.shellModule   - Electron shell (injected for tests).
 * @param {Function} [opts.homedirFn]   - Override for os.homedir (tests).
 * @returns {Promise<"reset"|"quit"|"error">}
 */
async function runSafeModePrompt({ count, dialogModule, appModule, shellModule, homedirFn }) {
  const homedir = homedirFn || (() => os.homedir());
  const gaiaDir = path.join(homedir(), ".gaia");
  let openFailure = "";
  try {
    for (;;) {
      const choice = dialogModule.showMessageBoxSync({
        type: "warning",
        title: "GAIA keeps crashing",
        message: `GAIA has crashed ${count} times in a row.`,
        detail:
          "Reset state clears the crash count and starts GAIA normally. " +
          `Open log folder shows the logs in ${gaiaDir} to attach to a bug report.` +
          openFailure,
        buttons: SAFE_MODE_BUTTONS,
        defaultId: SAFE_MODE_RESET,
        cancelId: SAFE_MODE_QUIT,
        noLink: true,
      });
      if (choice === SAFE_MODE_RESET) {
        fs.rmSync(counterPath(homedir), { force: true });
        appModule.relaunch();
        appModule.exit(0);
        return "reset";
      }
      if (choice === SAFE_MODE_OPEN_LOGS) {
        const err = await shellModule.openPath(gaiaDir);
        openFailure = err ? `\n\nCould not open ${gaiaDir}: ${err}` : "";
        continue;
      }
      appModule.exit(0);
      return "quit";
    }
  } catch (err) {
    const detail = (err && err.stack) || String(err);
    warn(`safe mode failed: ${detail}`);
    try { dialogModule.showErrorBox("GAIA could not recover", detail); } catch { } // intentional: exiting next
    appModule.exit(1);
    return "error";
  }
}

// ── Log-tee helper ────────────────────────────────────────────────────────────

/**
 * Attach an 'error' listener to a write stream so that asynchronous stream
 * errors (e.g. ERR_STREAM_WRITE_AFTER_END) are absorbed before they can
 * become uncaughtException.  This is the direct root-cause fix for #934.
 *
 * @param {object} opts
 * @param {EventEmitter} opts.stream   - The writable stream to guard.
 * @param {string}       opts.logPath  - Path for fallback error logging.
 * @note The internal WeakSet guard is module-scoped, so idempotency is
 *       process-global. A second call on the same stream is a no-op regardless
 *       of which caller site invokes it.
 */
const _teedStreams = new WeakSet();
function installLogTee({ stream, logPath }) {
  if (_teedStreams.has(stream)) return;
  _teedStreams.add(stream);
  stream.on("error", (err) => {
    const detail = (err && err.message) || (err && err.stack) || String(err);
    appendLog(logPath, `[${new Date().toISOString()}] STREAM_ERROR ${detail}`);
  });
}

module.exports = {
  installSafetyNet,
  installLogTee,
  FATAL_EXIT_GRACE_MS,
  readCount,
  runSafeModePrompt,
  SAFE_MODE_THRESHOLD,
};
