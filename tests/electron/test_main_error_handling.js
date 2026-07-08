// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * Tests for main-safety-net.cjs — top-level Electron main-process error handling.
 *
 * Root cause documented in issue #934: installMainLogTee()'s write stream emits
 * 'error' events asynchronously (not synchronous throws), so the wrap() try/catch
 * doesn't catch ERR_STREAM_WRITE_AFTER_END. Without a process.on('uncaughtException')
 * handler, this shows Electron's bare "A JavaScript error occurred" dialog.
 *
 * Tests are hermetic: all I/O is in a tmp directory; dialog and app are injected.
 * Tests import main-safety-net.cjs directly (no main.cjs side effects).
 */

"use strict";

const path = require("path");
const fs = require("fs");
const os = require("os");
const { EventEmitter } = require("events");

// ── Helpers ──────────────────────────────────────────────────────────────────

/** Create an isolated tmp directory for this test run. */
function makeTmpDir() {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "gaia-934-test-"));
  return dir;
}

/** Build a fresh mock dialog module. */
function mockDialog() {
  return {
    showErrorBox: jest.fn(),
    showMessageBoxSync: jest.fn(() => 0),
  };
}

/** Build a mock app module with controllable isReady(). */
function mockApp(isReady = false) {
  const emitter = new EventEmitter();
  emitter.isReady = jest.fn(() => isReady);
  return emitter;
}

// ── Module under test ────────────────────────────────────────────────────────
//
// This require MUST stay here (not inside beforeEach) so Jest's module cache
// can be cleared between tests that change process.on listener state.
// Each test that needs isolation calls jest.resetModules() + re-requires.

const SAFETY_NET_PATH = "../../src/gaia/apps/webui/main-safety-net.cjs";

// ── Test suite ───────────────────────────────────────────────────────────────

describe("installSafetyNet", () => {
  let tmpDir;
  let logPath;
  let addedListeners;

  beforeEach(() => {
    jest.resetModules();
    tmpDir = makeTmpDir();
    logPath = path.join(tmpDir, "electron-main.log");

    // Track listeners added so we can remove them after each test.
    addedListeners = [];
    const origOn = process.on.bind(process);
    jest.spyOn(process, "on").mockImplementation((event, handler) => {
      addedListeners.push({ event, handler });
      origOn(event, handler);
    });
  });

  afterEach(() => {
    // Remove any listeners installed by installSafetyNet to avoid cross-test leakage.
    addedListeners.forEach(({ event, handler }) => {
      process.removeListener(event, handler);
    });
    jest.restoreAllMocks();
    // Clean up tmp dir.
    try { fs.rmSync(tmpDir, { recursive: true, force: true }); } catch { /* ignore */ }
  });

  // ── Test 1: wires uncaughtException ────────────────────────────────────────

  test("registers uncaughtException listener", () => {
    const { installSafetyNet } = require(SAFETY_NET_PATH);
    const dialog = mockDialog();
    const app = mockApp(false);

    installSafetyNet({ logPath, dialogModule: dialog, appModule: app });

    const events = addedListeners.map((l) => l.event);
    expect(events).toContain("uncaughtException");
  });

  // ── Test 2: wires unhandledRejection ───────────────────────────────────────

  test("registers unhandledRejection listener", () => {
    const { installSafetyNet } = require(SAFETY_NET_PATH);
    const dialog = mockDialog();
    const app = mockApp(false);

    installSafetyNet({ logPath, dialogModule: dialog, appModule: app });

    const events = addedListeners.map((l) => l.event);
    expect(events).toContain("unhandledRejection");
  });

  // ── Test 3: re-entry guard ─────────────────────────────────────────────────
  // fatal() must not recurse if it is re-invoked while already running.
  // We trigger genuine re-entry by emitting a second uncaughtException from
  // inside showErrorBox — at that point _inFatalHandler is true, so the
  // second invocation must call process.exit(2) without touching the dialog.

  test("re-entry guard prevents recursive dialog on second call", () => {
    const { installSafetyNet } = require(SAFETY_NET_PATH);
    const dialog = mockDialog();
    const app = mockApp(false);
    const exitSpy = jest.spyOn(process, "exit").mockImplementation(() => {});

    installSafetyNet({ logPath, dialogModule: dialog, appModule: app });

    // Trigger re-entry: the first showErrorBox call emits a second
    // uncaughtException synchronously while _inFatalHandler is still true.
    dialog.showErrorBox.mockImplementationOnce(() => {
      process.emit("uncaughtException", new Error("re-entrant error"));
    });

    process.emit("uncaughtException", new Error("original error"));

    // showErrorBox called exactly once — re-entrant call bailed before dialog.
    expect(dialog.showErrorBox).toHaveBeenCalledTimes(1);
    // process.exit called with 2 for the re-entrant bail, then 1 for the outer.
    expect(exitSpy).toHaveBeenCalledWith(2);

    exitSpy.mockRestore();
  });

  // ── Test 4: showErrorBox used when app is NOT ready ────────────────────────
  // Pre-app.ready on Windows, showMessageBoxSync silently no-ops;
  // showErrorBox must be used in that window.

  test("uses showErrorBox (not showMessageBoxSync) when app.isReady() is false", () => {
    const { installSafetyNet } = require(SAFETY_NET_PATH);
    const dialog = mockDialog();
    const app = mockApp(false); // NOT ready
    const exitSpy = jest.spyOn(process, "exit").mockImplementation(() => {});

    installSafetyNet({ logPath, dialogModule: dialog, appModule: app });
    process.emit("uncaughtException", new Error("pre-ready crash"));

    expect(dialog.showErrorBox).toHaveBeenCalledTimes(1);
    expect(dialog.showMessageBoxSync).not.toHaveBeenCalled();

    exitSpy.mockRestore();
  });

  // ── Test 5: showMessageBoxSync used when app IS ready ──────────────────────
  // After app.ready fires, the full dialog with action buttons should appear.

  test("uses showMessageBoxSync when app.isReady() is true", () => {
    const { installSafetyNet } = require(SAFETY_NET_PATH);
    const dialog = mockDialog();
    const app = mockApp(true); // ready
    const exitSpy = jest.spyOn(process, "exit").mockImplementation(() => {});

    installSafetyNet({ logPath, dialogModule: dialog, appModule: app });
    process.emit("uncaughtException", new Error("post-ready crash"));

    expect(dialog.showMessageBoxSync).toHaveBeenCalledTimes(1);

    exitSpy.mockRestore();
  });

  // ── Test 6: crash-loop counter increments ─────────────────────────────────
  // Each fatal call increments the counter in the startup-failures JSON file.

  test("crash-loop counter increments on each fatal", () => {
    jest.resetModules();
    const { installSafetyNet } = require(SAFETY_NET_PATH);
    const dialog = mockDialog();
    const app = mockApp(false);
    const exitSpy = jest.spyOn(process, "exit").mockImplementation(() => {});
    const counterPath = path.join(tmpDir, ".gaia", "electron-startup-failures.json");

    installSafetyNet({
      logPath,
      dialogModule: dialog,
      appModule: app,
      homedirFn: () => tmpDir,
    });

    process.emit("uncaughtException", new Error("crash 1"));
    const after1 = JSON.parse(fs.readFileSync(counterPath, "utf8"));
    expect(after1.count).toBe(1);

    // Remove instance 1's listeners so instance 2's fatal() runs cleanly
    // without relying on instance 1's _inFatalHandler being stuck true.
    addedListeners.forEach(({ event, handler }) => process.removeListener(event, handler));
    addedListeners.length = 0;

    jest.resetModules();
    const { installSafetyNet: installSafetyNet2 } = require(SAFETY_NET_PATH);
    installSafetyNet2({
      logPath,
      dialogModule: dialog,
      appModule: app,
      homedirFn: () => tmpDir,
    });

    process.emit("uncaughtException", new Error("crash 2"));
    const after2 = JSON.parse(fs.readFileSync(counterPath, "utf8"));
    expect(after2.count).toBe(2);

    exitSpy.mockRestore();
  });

  // ── Test 7: counter resets on browser-window-focus (NOT after loadApp) ─────
  // Resetting after loadApp() is too early — the user may crash before their
  // first interaction. Reset must happen on 'browser-window-focus' instead.

  test("crash-loop counter resets on browser-window-focus, not on module load", () => {
    const { installSafetyNet } = require(SAFETY_NET_PATH);
    const dialog = mockDialog();
    const app = mockApp(false);
    const exitSpy = jest.spyOn(process, "exit").mockImplementation(() => {});
    const gaiaDir = path.join(tmpDir, ".gaia");
    const counterPath = path.join(gaiaDir, "electron-startup-failures.json");

    // Seed an existing count of 2.
    fs.mkdirSync(gaiaDir, { recursive: true });
    fs.writeFileSync(counterPath, JSON.stringify({ count: 2 }));

    installSafetyNet({
      logPath,
      dialogModule: dialog,
      appModule: app,
      homedirFn: () => tmpDir,
    });

    // Counter should NOT reset on install alone.
    const afterInstall = JSON.parse(fs.readFileSync(counterPath, "utf8"));
    expect(afterInstall.count).toBe(2);

    // Counter MUST reset when 'browser-window-focus' fires on app.
    app.emit("browser-window-focus");
    const afterFocus = JSON.parse(fs.readFileSync(counterPath, "utf8"));
    expect(afterFocus.count).toBe(0);

    exitSpy.mockRestore();
  });

  // ── Test 8: render-process-gone handler installed ─────────────────────────
  // Renderer crashes don't fire uncaughtException; they fire
  // app.on('render-process-gone'). Must be routed through fatal handler.

  test("installs render-process-gone handler on app", () => {
    const { installSafetyNet } = require(SAFETY_NET_PATH);
    const dialog = mockDialog();
    const app = mockApp(false);
    const onSpy = jest.spyOn(app, "on");
    const exitSpy = jest.spyOn(process, "exit").mockImplementation(() => {});

    installSafetyNet({ logPath, dialogModule: dialog, appModule: app });

    const registeredEvents = onSpy.mock.calls.map(([evt]) => evt);
    expect(registeredEvents).toContain("render-process-gone");

    exitSpy.mockRestore();
  });

  // ── Test 9: child-process-gone handler installed ───────────────────────────
  // GPU-process crashes fire app.on('child-process-gone'), not uncaughtException.

  test("installs child-process-gone handler on app", () => {
    const { installSafetyNet } = require(SAFETY_NET_PATH);
    const dialog = mockDialog();
    const app = mockApp(false);
    const onSpy = jest.spyOn(app, "on");
    const exitSpy = jest.spyOn(process, "exit").mockImplementation(() => {});

    installSafetyNet({ logPath, dialogModule: dialog, appModule: app });

    const registeredEvents = onSpy.mock.calls.map(([evt]) => evt);
    expect(registeredEvents).toContain("child-process-gone");

    exitSpy.mockRestore();
  });

  // ── Test 9b: GPU child-process crash is non-fatal ──────────────────────────
  // A GPU-process crash is recoverable (Chromium relaunches it and falls back
  // to software rendering). It fires routinely in GPU-less environments
  // (Windows Sandbox, VMs, RDP), so it must NOT kill the app or show a dialog.

  test("child-process-gone of type GPU does not trigger fatal", () => {
    const { installSafetyNet } = require(SAFETY_NET_PATH);
    const dialog = mockDialog();
    const app = mockApp(true);
    const exitSpy = jest.spyOn(process, "exit").mockImplementation(() => {});

    installSafetyNet({ logPath, dialogModule: dialog, appModule: app });
    app.emit("child-process-gone", {}, { type: "GPU", reason: "crashed" });

    // No crash dialog, no process exit — the app keeps running.
    expect(dialog.showMessageBoxSync).not.toHaveBeenCalled();
    expect(dialog.showErrorBox).not.toHaveBeenCalled();
    expect(exitSpy).not.toHaveBeenCalled();

    // The crash is still recorded forensically.
    const logContent = fs.readFileSync(logPath, "utf8");
    expect(logContent).toMatch(/GPU_PROCESS_GONE/);

    exitSpy.mockRestore();
  });

  // ── Test 9c: non-GPU child-process crash is still fatal ────────────────────
  // Only GPU crashes are recoverable; a crashed utility/other child process
  // still routes through fatal() as before.

  test("child-process-gone of a non-GPU type still triggers fatal", () => {
    const { installSafetyNet } = require(SAFETY_NET_PATH);
    const dialog = mockDialog();
    const app = mockApp(true);
    const exitSpy = jest.spyOn(process, "exit").mockImplementation(() => {});

    installSafetyNet({ logPath, dialogModule: dialog, appModule: app });
    app.emit("child-process-gone", {}, { type: "Utility", reason: "crashed" });

    expect(dialog.showMessageBoxSync).toHaveBeenCalledTimes(1);

    exitSpy.mockRestore();
  });

  // ── Test 10: fatal handler writes to log before showing dialog ─────────────
  // If dialog.showErrorBox itself crashes, the log must already have the entry.

  test("writes FATAL line to logPath before calling dialog", () => {
    const { installSafetyNet } = require(SAFETY_NET_PATH);
    const dialog = mockDialog();
    let logWritten = false;
    dialog.showErrorBox.mockImplementation(() => {
      // At the moment showErrorBox is called, the log must already be written.
      logWritten = fs.existsSync(logPath) &&
        fs.readFileSync(logPath, "utf8").includes("FATAL");
    });
    const app = mockApp(false);
    const exitSpy = jest.spyOn(process, "exit").mockImplementation(() => {});

    installSafetyNet({ logPath, dialogModule: dialog, appModule: app });
    process.emit("uncaughtException", new Error("test fatal"));

    expect(logWritten).toBe(true);

    exitSpy.mockRestore();
  });

  // ── Test 11: log tee stream gets error listener (root cause fix) ───────────
  // The #934 root cause: installMainLogTee()'s stream.write() emits 'error'
  // asynchronously; the try/catch in wrap() doesn't catch it. A stream 'error'
  // listener prevents ERR_STREAM_WRITE_AFTER_END from becoming uncaughtException.

  test("installLogTee attaches an error listener to the write stream", () => {
    const { installLogTee } = require(SAFETY_NET_PATH);
    expect(typeof installLogTee).toBe("function");
    const mockStream = new EventEmitter();
    mockStream.write = jest.fn();
    mockStream.end = jest.fn();

    installLogTee({ stream: mockStream, logPath });

    // The stream must have at least one 'error' listener so errors don't
    // become uncaughtException.
    expect(mockStream.listenerCount("error")).toBeGreaterThan(0);

    // The listener must actually write to logPath — this is the root-cause fix
    // for #934, not just its prerequisite.
    mockStream.emit("error", new Error("boom"));
    const logContent = fs.readFileSync(logPath, "utf8");
    expect(logContent).toMatch(/STREAM_ERROR/);
    expect(logContent).toMatch(/boom/);
  });

  // ── Test 12: unhandledRejection wraps non-Error reasons ───────────────────
  // process.emit('unhandledRejection', "string") must not crash the handler.

  test("unhandledRejection handler coerces non-Error reason to Error", () => {
    const { installSafetyNet } = require(SAFETY_NET_PATH);
    const dialog = mockDialog();
    const app = mockApp(false);
    const exitSpy = jest.spyOn(process, "exit").mockImplementation(() => {});

    installSafetyNet({ logPath, dialogModule: dialog, appModule: app });

    // Emit with a plain string (not an Error instance).
    expect(() => {
      process.emit("unhandledRejection", "plain string rejection");
    }).not.toThrow();

    expect(dialog.showErrorBox).toHaveBeenCalledTimes(1);
    const [, detail] = dialog.showErrorBox.mock.calls[0];
    expect(detail).toContain("plain string rejection");

    exitSpy.mockRestore();
  });

  // ── Test 13: installSafetyNet returns { fatal } ────────────────────────────
  // main.cjs destructures { fatal: _fatalHandler } and routes
  // app.whenReady().catch() through it. If a refactor stops returning fatal,
  // _fatalHandler becomes undefined and the catch silently no-ops.

  test("returns { fatal } function so main.cjs can route .catch() through it", () => {
    const { installSafetyNet } = require(SAFETY_NET_PATH);
    const result = installSafetyNet({
      logPath,
      dialogModule: mockDialog(),
      appModule: mockApp(false),
    });
    expect(typeof result.fatal).toBe("function");
  });
});
