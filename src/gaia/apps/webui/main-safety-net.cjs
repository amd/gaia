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
 */

"use strict";

const fs = require("fs");
const path = require("path");
const os = require("os");

// ── Counter helpers ───────────────────────────────────────────────────────────
// The counter is currently forensic only — post-mortem grep of
// ~/.gaia/electron-startup-failures.json.
// TODO(#938): on next launch, if count >= 3, skip log-tee init and show
// a "reset state?" dialog (safe-mode entry point).

function counterPath(homedir) {
  return path.join(homedir(), ".gaia", "electron-startup-failures.json");
}

function readCount(homedir) {
  try {
    return JSON.parse(fs.readFileSync(counterPath(homedir), "utf8")).count || 0;
  } catch {
    return 0;
  }
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

// ── Core installer ────────────────────────────────────────────────────────────

/**
 * Install the safety-net handlers on the current process.
 *
 * @param {object} opts
 * @param {string}   opts.logPath       - Path to append FATAL lines into.
 * @param {object}   opts.dialogModule  - Electron dialog (injected for tests).
 * @param {object}   opts.appModule     - Electron app EventEmitter (injected).
 * @param {Function} [opts.homedirFn]   - Override for os.homedir (tests).
 */
function installSafetyNet({ logPath, dialogModule, appModule, homedirFn }) {
  const homedir = homedirFn || (() => os.homedir());

  // Per-handler re-entry guard (closure-scoped — each installSafetyNet
  // call gets its own, intentionally; see test_main_error_handling.js).
  let _inFatalHandler = false;

  function fatal(err) {
    if (_inFatalHandler) {
      try { process.exit(2); } catch { }
      return;
    }
    _inFatalHandler = true;

    const stack = (err && err.stack) ? err.stack : String(err);
    const ts = new Date().toISOString();

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

    try { process.exit(1); } catch { } // intentional: fatal path
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

module.exports = { installSafetyNet, installLogTee };
