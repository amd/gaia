// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * Source-level contract for main.cjs (it cannot be required without
 * Electron): a backend that exits on its own goes to the restart supervisor,
 * the crash dialog waits until restarts run out, and the exit leaves evidence.
 */

const fs = require("fs");
const path = require("path");

// The slicing below matches "\n"; a CRLF checkout would never match it.
const main = fs
  .readFileSync(path.join(__dirname, "..", "..", "src", "gaia", "apps", "webui", "main.cjs"), "utf8")
  .replace(/\r\n/g, "\n");
const preload = fs.readFileSync(
  path.join(__dirname, "..", "..", "src", "gaia", "apps", "webui", "preload.cjs"),
  "utf8"
);

function block(start) {
  const i = main.indexOf(start);
  expect(i).toBeGreaterThan(-1);
  return main.slice(i, main.indexOf("\n  });\n", i));
}

test("main.cjs and preload.cjs compile, since the checks below only read their text", () => {
  const vm = require("vm");
  // Wrapped like Node's CommonJS loader, so a top-level return is legal.
  for (const [name, text] of [["main.cjs", main], ["preload.cjs", preload]]) {
    expect(() => new vm.Script(`(function (exports, require, module, __filename, __dirname) {${text}\n})`, { filename: name })).not.toThrow();
  }
});

test("an unexpected exit goes to the supervisor", () => {
  const onExit = block('child.on("exit"');
  expect(onExit).toMatch(/const crashed = !isIntentionalKill && !isQuitting;/);
  expect(onExit).toMatch(/if \(crashed\) backendSupervisor\.crashed\(\{ code, signal \}\);/);
  // A later backend's handle is never cleared by an earlier one's exit.
  expect(onExit).toMatch(/if \(backendProcess === child\) backendProcess = null;/);
});

test("the backend's last stderr lines reach app.log once its pipes drain", () => {
  const onClose = block('child.once("close"');
  expect(onClose).toMatch(/backendStderrTail/);
  expect(onClose).toMatch(/console\.error/);
});

test("the backend's output is unbuffered, so its last lines aren't lost", () => {
  expect(main).toMatch(/PYTHONUNBUFFERED: "1"/);
});

test("a quit stops the supervisor before a pending restart can spawn", () => {
  const cleanup = main.slice(main.indexOf("async function cleanup()"));
  expect(cleanup.slice(0, 200)).toMatch(/backendSupervisor\.stop\(\);/);
  expect(main).toMatch(/onQuitting: \(\) => \{[\s\S]{0,300}backendSupervisor\.stop\(\);/);
});

test("the renderer can read the state, follow it and ask for Restart GAIA", () => {
  for (const channel of ["backend:get-state", "backend:restart"]) {
    expect(main).toContain(`ipcMain.handle("${channel}"`);
    expect(preload).toContain(`ipcRenderer.invoke("${channel}")`);
  }
  expect(main).toContain('webContents.send("backend:state"');
  expect(preload).toContain('onEvent("backend:state", cb)');
});

// ── Behaviour: main.cjs's own handlers, run against fakes ────────────────────

const vm = require("vm");
const { EventEmitter } = require("events");
const { createBackendSupervisor } = require("../../src/gaia/apps/webui/services/backend-supervisor.cjs");
const { showBackendCrashDialog } = require("../../src/gaia/apps/webui/services/backend-crash-dialog.cjs");

/** The text of a top-level `function name(` declaration in main.cjs. */
function topLevelFunction(signature) {
  const i = main.indexOf(signature);
  expect(i).toBeGreaterThan(-1);
  return main.slice(i, main.indexOf("\n}\n", i) + 2);
}

function runInMain(source, globals) {
  const context = vm.createContext({ console: { log() {}, error() {} }, ...globals });
  return { value: vm.runInContext(source, context), context };
}

test("restarts that keep failing show the crash dialog and offer the rollback, once", async () => {
  const onStopped = main.match(/onStopped: (\(state\) => \{[\s\S]*?\n  \}),/);
  expect(onStopped).not.toBeNull();
  const autoUpdater = { reportStartFailure: jest.fn() };
  const dialog = { showMessageBox: jest.fn(async () => ({ response: 2 })), showErrorBox: jest.fn() };
  const app = { relaunch: jest.fn(), quit: jest.fn() };
  const { value: handler } = runInMain(
    `${topLevelFunction("async function handleBackendCrash(")}\n(${onStopped[1]})`,
    {
      autoUpdater,
      showBackendCrashDialog,
      dialog,
      app,
      backendStderrTail: ["Fatal Python error: Segmentation fault"],
      portManager: { writeDiagnosticsBundle: jest.fn() },
      mainWindow: null,
      fatalQuitting: false,
    }
  );

  const starts = [];
  const timers = [];
  const sup = createBackendSupervisor({
    start: () => new Promise((resolve, reject) => starts.push({ resolve, reject })),
    onState: () => {},
    onStopped: handler,
    backoffMs: [10],
    setTimer: (fn) => timers.push(fn),
    clearTimer: () => {},
  });
  sup.running(4200);
  sup.crashed({ code: 3221225477, signal: null });
  for (let i = 0; i < 3; i++) {
    timers.shift()();
    sup.crashed({ code: 1, signal: null });
    starts[i].reject(new Error("The backend exited (code 1, signal null) before it answered."));
    await new Promise((r) => setImmediate(r));
  }
  await new Promise((r) => setImmediate(r));

  expect(starts).toHaveLength(3);
  expect(sup.getState().status).toBe("stopped");
  expect(autoUpdater.reportStartFailure).toHaveBeenCalledTimes(1);
  expect(dialog.showMessageBox).toHaveBeenCalledTimes(1);
  expect(dialog.showMessageBox.mock.calls[0][0].detail).toMatch(/exit code 1\)[\s\S]*Segmentation fault/);
  expect(app.quit).toHaveBeenCalledTimes(1);
});

test("the exit handler leaves the dialog to the supervisor, so it can't show twice", () => {
  const onExit = block('child.on("exit"');
  expect(onExit).toMatch(/if \(crashed\) backendSupervisor\.crashed\(\{ code, signal \}\);/);
  expect(onExit).not.toMatch(/handleBackendCrash/);
});

function restartHarness({ quitWhileSpawning }) {
  const child = { pid: 4321, exitCode: null, signalCode: null };
  const portManager = { killBackend: jest.fn(async () => {}) };
  const supervisor = { stop: jest.fn() };
  const updater = new EventEmitter();
  const globals = {
    shutdownStarted: false,
    isQuitting: false,
    updateQuitStarted: false,
    isIntentionalKill: false,
    backendProcess: null,
    backendStarting: null,
    backendSupervisor: supervisor,
    electronAutoUpdater: updater,
    portManager,
    waitForBackend: jest.fn(async () => true),
    trayManager: null,
    backendPort: 4300,
    RESTART_TIMEOUT: 90000,
  };
  const updaterHook = main.match(/electronAutoUpdater\.on\("before-quit-for-update", \(\) => \{[\s\S]*?\n\}\);/);
  expect(updaterHook).not.toBeNull();
  const { value: restartBackend, context } = runInMain(
    `${topLevelFunction("function quitStarted(")}\n${updaterHook[0]}\n` +
      `${topLevelFunction("async function restartBackend(")}\nrestartBackend`,
    globals
  );
  context.startBackend = () => {
    quitWhileSpawning(context, updater);
    return Promise.resolve(child);
  };
  return { restartBackend, context, child, portManager, supervisor };
}

test.each([
  ["the user quits", (ctx) => { ctx.isQuitting = true; }],
  ["cleanup begins", (ctx) => { ctx.shutdownStarted = true; }],
  ["an update starts installing", (_ctx, updater) => updater.emit("before-quit-for-update")],
])("a backend spawned while %s is stopped, not left running", async (_name, quitWhileSpawning) => {
  const h = restartHarness({ quitWhileSpawning });
  await expect(h.restartBackend()).rejects.toThrow(/quitting/);
  // The quit's own cleanup waits on the same spawn.
  expect(h.context.backendStarting).toBeInstanceOf(Promise);
  await expect(h.context.backendStarting).resolves.toBe(h.child);
  expect(h.portManager.killBackend).toHaveBeenCalledWith(h.child);
  expect(h.context.isIntentionalKill).toBe(true);
  expect(h.context.backendProcess).toBeNull();
  expect(h.context.waitForBackend).not.toHaveBeenCalled();
});

test("an update install stops the supervisor before it can spawn again", () => {
  const h = restartHarness({ quitWhileSpawning: () => {} });
  expect(h.context.updateQuitStarted).toBe(false);
  h.context.electronAutoUpdater.emit("before-quit-for-update");
  expect(h.context.updateQuitStarted).toBe(true);
  expect(h.supervisor.stop).toHaveBeenCalledTimes(1);
});

test("with no quit under way, a restart adopts the new backend", async () => {
  const h = restartHarness({ quitWhileSpawning: () => {} });
  await expect(h.restartBackend()).resolves.toEqual({ port: 4300 });
  expect(h.context.backendProcess).toBe(h.child);
  expect(h.portManager.killBackend).not.toHaveBeenCalled();
});
