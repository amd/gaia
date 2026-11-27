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
  expect(cleanup.slice(0, 220)).toMatch(/backendHealthMonitor\.stop\(\);[\s\S]{0,80}backendSupervisor\.stop\(\);/);
  expect(main).toMatch(/onQuitting: \(\) => \{[\s\S]{0,300}backendHealthMonitor\.stop\(\);[\s\S]{0,80}backendSupervisor\.stop\(\);/);
  expect(main).toMatch(/before-quit-for-update"[\s\S]{0,150}backendHealthMonitor\.stop\(\);[\s\S]{0,80}backendSupervisor\.stop\(\);/);
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

test("health probes accept only HTTP 200 and report request failures", async () => {
  for (const [statusCode, succeeds] of [[200, true], [503, false]]) {
    const req = new EventEmitter();
    req.destroy = jest.fn((error) => req.emit("error", error));
    const http = {
      get: jest.fn((_url, onResponse) => {
        const response = new EventEmitter();
        response.statusCode = statusCode;
        response.complete = true;
        response.destroy = jest.fn();
        response.resume = jest.fn(() => setImmediate(() => response.emit("end")));
        setImmediate(() => onResponse(response));
        return req;
      }),
    };
    const { value: check } = runInMain(`${topLevelFunction("function checkBackendHealth(")}\ncheckBackendHealth`, {
      healthCheckUrl: "http://localhost:4200/api/health",
      BACKEND_HEALTH_CHECK_TIMEOUT: 1000,
      require: () => http,
      setTimeout,
      clearTimeout,
    });

    if (succeeds) await expect(check()).resolves.toBeUndefined();
    else await expect(check()).rejects.toThrow(/status 503/);
    expect(http.get).toHaveBeenCalledWith("http://localhost:4200/api/health", expect.any(Function));
  }

  const failedRequest = new EventEmitter();
  failedRequest.destroy = jest.fn();
  const failedHttp = { get: jest.fn(() => failedRequest) };
  const { value: checkFailedRequest } = runInMain(
    `${topLevelFunction("function checkBackendHealth(")}\ncheckBackendHealth`,
    {
      healthCheckUrl: "http://localhost:4200/api/health",
      BACKEND_HEALTH_CHECK_TIMEOUT: 1000,
      require: () => failedHttp,
      setTimeout,
      clearTimeout,
    }
  );
  const requestFailure = checkFailedRequest();
  failedRequest.emit("error", new Error("connection reset"));
  await expect(requestFailure).rejects.toThrow(/connection reset/);

  const req = new EventEmitter();
  req.destroy = jest.fn((error) => req.emit("error", error));
  const http = { get: jest.fn(() => req) };
  const { value: check } = runInMain(`${topLevelFunction("function checkBackendHealth(")}\ncheckBackendHealth`, {
    healthCheckUrl: "http://localhost:4200/api/health",
    BACKEND_HEALTH_CHECK_TIMEOUT: 1,
    require: () => http,
    setTimeout,
    clearTimeout,
  });
  await expect(check()).rejects.toThrow(/did not answer within 2 s/);
  expect(req.destroy).toHaveBeenCalledTimes(1);
});

test.each([
  ["aborted", new Error("Health endpoint response was aborted.")],
  ["error", new Error("response stream failed")],
  ["close", new Error("Health endpoint response closed before completion.")],
])("unfinished health responses reject on %s", async (event, expectedError) => {
  const req = new EventEmitter();
  req.destroy = jest.fn((error) => req.emit("error", error));
  const response = new EventEmitter();
  response.statusCode = 200;
  response.complete = false;
  response.resume = jest.fn();
  response.destroy = jest.fn();
  const http = {
    get: jest.fn((_url, onResponse) => {
      setImmediate(() => onResponse(response));
      return req;
    }),
  };
  const { value: check } = runInMain(`${topLevelFunction("function checkBackendHealth(")}\ncheckBackendHealth`, {
    healthCheckUrl: "http://localhost:4200/api/health",
    BACKEND_HEALTH_CHECK_TIMEOUT: 1000,
    require: () => http,
    setTimeout,
    clearTimeout,
  });

  const pending = check();
  await new Promise(setImmediate);
  response.emit(event, event === "error" ? expectedError : undefined);

  await expect(pending).rejects.toThrow(expectedError.message);
  expect(response.resume).toHaveBeenCalledTimes(1);
});

test("a 200 response that never ends times out and destroys the request", async () => {
  const req = new EventEmitter();
  req.destroy = jest.fn((error) => req.emit("error", error));
  const response = new EventEmitter();
  response.statusCode = 200;
  response.resume = jest.fn();
  response.destroy = jest.fn();
  const http = {
    get: jest.fn((_url, onResponse) => {
      setImmediate(() => onResponse(response));
      return req;
    }),
  };
  const { value: check } = runInMain(`${topLevelFunction("function checkBackendHealth(")}\ncheckBackendHealth`, {
    healthCheckUrl: "http://localhost:4200/api/health",
    BACKEND_HEALTH_CHECK_TIMEOUT: 10,
    require: () => http,
    setTimeout,
    clearTimeout,
  });

  await expect(check()).rejects.toThrow(/did not answer within 2 s/);
  expect(response.resume).toHaveBeenCalledTimes(1);
  expect(req.destroy).toHaveBeenCalledTimes(1);
});

test("stopping a health probe after 200 headers aborts its unfinished response", async () => {
  const req = new EventEmitter();
  req.destroy = jest.fn((error) => req.emit("error", error));
  const response = new EventEmitter();
  response.statusCode = 200;
  response.resume = jest.fn();
  response.destroy = jest.fn();
  const http = {
    get: jest.fn((_url, onResponse) => {
      setImmediate(() => onResponse(response));
      return req;
    }),
  };
  const { value: check } = runInMain(`${topLevelFunction("function checkBackendHealth(")}\ncheckBackendHealth`, {
    healthCheckUrl: "http://localhost:4200/api/health",
    BACKEND_HEALTH_CHECK_TIMEOUT: 1000,
    require: () => http,
    setTimeout,
    clearTimeout,
  });
  const controller = new AbortController();
  const pending = check(controller.signal);
  await new Promise(setImmediate);
  controller.abort();

  await expect(pending).rejects.toThrow(/cancelled/);
  expect(response.resume).toHaveBeenCalledTimes(1);
  expect(req.destroy).toHaveBeenCalledTimes(1);
});

test("stopping a health probe aborts its outstanding local request", async () => {
  const req = new EventEmitter();
  req.destroy = jest.fn((error) => req.emit("error", error));
  const http = { get: jest.fn(() => req) };
  const { value: check } = runInMain(`${topLevelFunction("function checkBackendHealth(")}\ncheckBackendHealth`, {
    healthCheckUrl: "http://localhost:4200/api/health",
    BACKEND_HEALTH_CHECK_TIMEOUT: 1000,
    require: () => http,
    setTimeout,
    clearTimeout,
  });
  const controller = new AbortController();
  const pending = check(controller.signal);
  controller.abort();
  await expect(pending).rejects.toThrow(/cancelled/);
  expect(req.destroy).toHaveBeenCalledTimes(1);
});

test("health monitoring starts after readiness or startup grace and feeds supervisor recovery", () => {
  expect(main).toMatch(/backendHealthMonitor = createBackendHealthMonitor\(\{\s*check: checkBackendHealth,/);
  expect(main).toMatch(/backendSupervisor\.healthFailed\(reason\);/);
  expect(main).toMatch(/backendHealthMonitoringEnabled && state\.status === "running"\) backendHealthMonitor\.start\(\)/);

  const from = main.indexOf("backendReady = await waitForBackend(STARTUP_TIMEOUT, () => !quitStarted());");
  const startup = main.slice(from, main.indexOf("if (showingInstallScreen)", from));
  expect(from).toBeGreaterThan(-1);
  expect(startup).toMatch(/if \(backendReady\) \{[\s\S]*enableBackendHealthMonitoring\(\);/);
  expect(startup).toMatch(/waitForBackend\(STARTUP_TIMEOUT \* 10, \(\) => !quitStarted\(\)\)\.then\(\(ready\) => \{[\s\S]*enableBackendHealthMonitoring\(\)/);
  expect(startup).toMatch(
    /healthFailed\(\s*new Error\("The backend health endpoint did not respond during the startup grace period\."\)\s*\)/
  );
});

function restartHarness({ quitWhileSpawning }) {
  const child = { pid: 4321, exitCode: null, signalCode: null };
  const portManager = { killBackend: jest.fn(async () => ({ killed: [], survivors: [], verified: true })) };
  const supervisor = { stop: jest.fn() };
  const updater = new EventEmitter();
  const globals = {
    shutdownStarted: false,
    isQuitting: false,
    updateQuitStarted: false,
    isIntentionalKill: false,
    backendProcess: null,
    backendCleanupPending: null,
    backendStarting: null,
    backendSupervisor: supervisor,
    backendHealthMonitor: { stop: jest.fn() },
    enableBackendHealthMonitoring: jest.fn(),
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
      `${topLevelFunction("async function stopBackendTree(")}\n` +
      `${topLevelFunction("async function restartBackend(")}\nrestartBackend`,
    globals
  );
  context.startBackend = jest.fn(() => {
    quitWhileSpawning(context, updater);
    return Promise.resolve(child);
  });
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
  expect(h.context.backendHealthMonitor.stop).toHaveBeenCalledTimes(1);
  expect(h.supervisor.stop).toHaveBeenCalledTimes(1);
});

test("a restart that finds no backend program fails as one no retry can fix", async () => {
  const { value: restartBackend, context } = runInMain(
    `${topLevelFunction("function missingBackendMessage(")}
${topLevelFunction("async function restartBackend(")}
restartBackend`,
    {
      backendInstaller: { BUNDLED_GAIA_BIN: "/opt/gaia/core/gaia", GAIA_BIN: "/home/you/.gaia/venv/bin/gaia" },
      backendStarting: null,
      backendProcess: null,
      backendCleanupPending: null,
    }
  );
  context.startBackend = () => Promise.resolve(null);
  const err = await restartBackend().catch((e) => e);
  expect(err.terminal).toBe(true);
  expect(err.message).toMatch(/^GAIA's backend program is missing/);
});

test("with no quit under way, a restart adopts the new backend", async () => {
  const h = restartHarness({ quitWhileSpawning: () => {} });
  await expect(h.restartBackend()).resolves.toEqual({ port: 4300 });
  expect(h.context.backendProcess).toBe(h.child);
  expect(h.context.enableBackendHealthMonitoring).toHaveBeenCalledTimes(1);
  expect(h.portManager.killBackend).not.toHaveBeenCalled();
});

function restartHealthHarness() {
  let retry;
  let monitoringEnabled = false;
  let supervisor;
  const child = { pid: 4321, exitCode: null, signalCode: null };
  const monitor = { start: jest.fn(), stop: jest.fn() };
  const enableMonitoring = jest.fn(() => {
    monitoringEnabled = true;
    if (supervisor.getState().status === "running") monitor.start();
  });
  const { value: restartBackend, context } = runInMain(
    `${topLevelFunction("function quitStarted(")}\n${topLevelFunction("async function stopBackendTree(")}\n` +
      `${topLevelFunction("async function restartBackend(")}\nrestartBackend`,
    {
      shutdownStarted: false,
      isQuitting: false,
      updateQuitStarted: false,
      isIntentionalKill: false,
      backendProcess: null,
      backendCleanupPending: null,
      backendStarting: null,
      backendSupervisor: null,
      backendHealthMonitor: monitor,
      enableBackendHealthMonitoring: enableMonitoring,
      portManager: { killBackend: jest.fn(async () => ({ killed: [], survivors: [], verified: true })) },
      waitForBackend: jest.fn(async () => true),
      trayManager: null,
      backendPort: 4300,
      RESTART_TIMEOUT: 90000,
    }
  );
  const supervisorRef = createBackendSupervisor({
    start: () => restartBackend(),
    onState: (state) => {
      if (monitoringEnabled && state.status === "running") monitor.start();
      else monitor.stop();
    },
    backoffMs: [10],
    setTimer: (fn, ms) => {
      retry = { fn, ms };
      return retry;
    },
    clearTimer: (timer) => {
      timer.cleared = true;
    },
  });
  supervisor = supervisorRef;
  context.backendSupervisor = supervisorRef;
  context.startBackend = jest.fn(() => Promise.resolve(child));
  return {
    supervisor: supervisorRef,
    context,
    child,
    monitor,
    enableMonitoring,
    get retry() {
      return retry;
    },
  };
}

test("automatic recovery after the startup child exits enables health monitoring", async () => {
  const h = restartHealthHarness();
  h.supervisor.running(4300);
  h.context.backendProcess = null;
  expect(h.monitor.start).not.toHaveBeenCalled();

  h.supervisor.crashed({ code: 1 });
  h.retry.fn();
  await new Promise(setImmediate);

  expect(h.context.backendProcess).toBe(h.child);
  expect(h.enableMonitoring).toHaveBeenCalledTimes(1);
  expect(h.supervisor.getState()).toMatchObject({ status: "running", port: 4300 });
  expect(h.monitor.start).toHaveBeenCalledTimes(1);
});

test("manual recovery after a missing backend program enables health monitoring", async () => {
  const h = restartHealthHarness();
  h.supervisor.failedToStart("GAIA's backend program is missing");
  h.supervisor.restartNow();
  await new Promise(setImmediate);

  expect(h.context.backendProcess).toBe(h.child);
  expect(h.enableMonitoring).toHaveBeenCalledTimes(1);
  expect(h.supervisor.getState()).toMatchObject({ status: "running", port: 4300 });
  expect(h.monitor.start).toHaveBeenCalledTimes(1);
});

test("a restart stops the live backend first, so a hung one is not left running beside the new one", async () => {
  const h = restartHarness({ quitWhileSpawning: () => {} });
  const hung = { pid: 9999, exitCode: null, signalCode: null };
  h.context.backendProcess = hung;
  await expect(h.restartBackend()).resolves.toEqual({ port: 4300 });
  expect(h.portManager.killBackend).toHaveBeenCalledWith(hung);
  expect(h.portManager.killBackend).toHaveBeenCalledTimes(1);
  expect(h.context.backendProcess).toBe(h.child);
});

test("a restart does not spawn another backend when the old process tree survives termination", async () => {
  const h = restartHarness({ quitWhileSpawning: () => {} });
  const hung = { pid: 9999, exitCode: null, signalCode: null };
  h.context.backendProcess = hung;
  h.portManager.killBackend.mockResolvedValue({ killed: [], survivors: [9999], verified: false });

  await expect(h.restartBackend()).rejects.toThrow(/process tree is still running.*9999/);
  expect(h.context.startBackend).not.toHaveBeenCalled();
  expect(h.context.backendProcess).toBe(hung);
  expect(h.context.backendCleanupPending).toBe(hung);
  expect(h.context.isIntentionalKill).toBe(false);
});

test("a restart retains the old backend handle when stopping it fails", async () => {
  const h = restartHarness({ quitWhileSpawning: () => {} });
  const hung = { pid: 9999, exitCode: null, signalCode: null };
  h.context.backendProcess = hung;
  h.portManager.killBackend.mockRejectedValue(new Error("termination denied"));

  await expect(h.restartBackend()).rejects.toThrow(/termination denied/);
  expect(h.context.startBackend).not.toHaveBeenCalled();
  expect(h.context.backendProcess).toBe(hung);
  expect(h.context.backendCleanupPending).toBe(hung);
  expect(h.context.isIntentionalKill).toBe(false);
});

test.each([
  ["the process tree still reports a survivor", { killed: [], survivors: [12345], verified: false }, /still running.*12345/],
  ["termination throws after the launcher exits", new Error("termination denied"), /termination denied/],
])("a retry keeps the old backend pending when %s", async (_condition, outcome, expectedError) => {
  const h = restartHarness({ quitWhileSpawning: () => {} });
  const launcher = { pid: 9999, exitCode: null, signalCode: null };
  h.context.backendProcess = launcher;
  h.portManager.killBackend.mockImplementation(async () => {
    // Simulate the launcher's exit listener clearing the active-process field
    // while the asynchronous tree termination is still in flight.
    h.context.backendProcess = null;
    if (outcome instanceof Error) throw outcome;
    return outcome;
  });

  await expect(h.restartBackend()).rejects.toThrow(expectedError);
  expect(h.context.backendCleanupPending).toBe(launcher);
  await expect(h.restartBackend()).rejects.toThrow(expectedError);

  expect(h.portManager.killBackend).toHaveBeenNthCalledWith(1, launcher);
  expect(h.portManager.killBackend).toHaveBeenNthCalledWith(2, launcher);
  expect(h.context.startBackend).not.toHaveBeenCalled();
  expect(h.context.backendCleanupPending).toBe(launcher);
});

test("a slow first launch keeps its grace period and enables monitoring after readiness", () => {
  const from = main.indexOf("backendReady = await waitForBackend(STARTUP_TIMEOUT, () => !quitStarted());");
  const startup = main.slice(from, main.indexOf("if (showingInstallScreen)", from));
  expect(from).toBeGreaterThan(-1);
  // A slow but healthy backend keeps its grace period and is monitored once ready.
  expect(startup).not.toMatch(/killBackend/);
  expect(startup).toMatch(/waitForBackend\(STARTUP_TIMEOUT \* 10,[\s\S]*if \(ready\) \{[\s\S]*autoUpdater\.clearStartFailure\(\);[\s\S]*enableBackendHealthMonitoring\(\);/);
  expect(main).toMatch(/if \(state\.status === "running"\) autoUpdater\.clearStartFailure\(\);/);
});

test("a crashed window is offered a reload; GAIA keeps running", () => {
  const net = main.slice(main.indexOf("installSafetyNet({"), main.indexOf("} catch (err) {", main.indexOf("installSafetyNet({")));
  expect(net).toMatch(/onRendererGone: \(contents, details\) => \{[\s\S]{0,80}handleRendererGone\(contents, details\)/);
  const handler = main.slice(main.indexOf("async function handleRendererGone("));
  const body = handler.slice(0, handler.indexOf("\n}\n"));
  // Only GAIA's own window is reloaded; anything else stays fatal.
  expect(body).toMatch(/contents !== mainWindow\.webContents[\s\S]{0,200}_fatalHandler\(/);
  // Reload builds the URL afresh, so a backend restarted on a new port is reached.
  expect(body).toMatch(/choice === "reload"[\s\S]{0,120}await loadApp\(\)/);
  // A reload that fails is logged, not turned into the stack-trace crash box: a page
  // that dies again while loading fires render-process-gone, which asks again.
  const reload = body.slice(body.indexOf('choice === "reload"'), body.indexOf("} else {"));
  expect(reload).toMatch(/try \{\s*await loadApp\(\);\s*\} catch \(err\) \{[\s\S]*console\.error\(/);
  expect(reload).not.toMatch(/_fatalHandler\(/);
  // Quit goes through the quit guard, which asks about work still running.
  expect(body).toMatch(/app\.quit\(\)/);
  expect(body).not.toMatch(/fatalQuitting = true/);
});

test("a missing backend program reaches the window as a stopped backend that names the fix", () => {
  // Launch: no process means no backend, and the supervisor tells the window.
  const launch = main.slice(main.indexOf("backendProcess = await backendStarting;"));
  expect(launch.slice(0, 300)).toMatch(
    /if \(backendProcess\) backendSupervisor\.running\(backendPort\);\s*else backendSupervisor\.failedToStart\(missingBackendMessage\(\)\);/
  );

  const states = [];
  const onStopped = jest.fn();
  const sup = createBackendSupervisor({ start: jest.fn(), onState: (s) => states.push(s), onStopped });
  const { value: message } = runInMain(`${topLevelFunction("function missingBackendMessage(")}\nmissingBackendMessage()`, {
    backendInstaller: { BUNDLED_GAIA_BIN: "/Applications/GAIA.app/Contents/Resources/core/gaia", GAIA_BIN: "/Users/you/.gaia/venv/bin/gaia" },
  });
  sup.failedToStart(message);
  const sent = states[states.length - 1];
  expect(sent).toMatchObject({ status: "stopped", port: null, attempt: 0 });
  expect(sent.lastError).toMatch(/^GAIA's backend program is missing/);
  expect(sent.lastError).toMatch(/Reinstall GAIA/);
  expect(sent.lastError).toContain("/Applications/GAIA.app/Contents/Resources/core/gaia");
  expect(sent.lastError).toContain("/Users/you/.gaia/venv/bin/gaia");
  // The window shows it; the backend-crash dialog is for a backend that ran and then stopped.
  expect(onStopped).not.toHaveBeenCalled();
});
