// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * Quitting GAIA runs its cleanup (stop the agents and the backend) exactly
 * once, however many quits arrive, and never hangs the quit forever (#3498).
 * The last block pins how main.cjs uses it, since main.cjs can't be required
 * without launching Electron.
 */

"use strict";

const fs = require("fs");
const path = require("path");
const { EventEmitter } = require("events");

const {
  installQuitCleanup,
  CLEANUP_TIMEOUT_MS,
} = require("../../src/gaia/apps/webui/services/quit-cleanup.cjs");
const { installSafetyNet } = require("../../src/gaia/apps/webui/main-safety-net.cjs");

/** An app whose quit() emits will-quit the way Electron does. */
function fakeApp() {
  const app = new EventEmitter();
  app.exited = 0;
  app.quit = jest.fn(() => {
    let prevented = false;
    app.emit("will-quit", { preventDefault: () => (prevented = true) });
    if (!prevented) app.exited += 1;
  });
  return app;
}

const quietLogger = { log: () => {}, error: jest.fn() };

function deferred() {
  let resolve;
  const promise = new Promise((r) => (resolve = r));
  return { promise, resolve };
}

describe("installQuitCleanup", () => {
  test("holds the quit until cleanup finishes, then quits once", async () => {
    const app = fakeApp();
    const gate = deferred();
    const cleanup = jest.fn(() => gate.promise);
    const state = installQuitCleanup({ app, cleanup, logger: quietLogger });

    app.quit();
    expect(app.exited).toBe(0);
    gate.resolve();
    await state.running;
    expect(app.exited).toBe(1);
    expect(cleanup).toHaveBeenCalledTimes(1);
  });

  test("a second quit during cleanup does not run cleanup again or quit early", async () => {
    const app = fakeApp();
    const gate = deferred();
    const cleanup = jest.fn(() => gate.promise);
    const state = installQuitCleanup({ app, cleanup, logger: quietLogger });

    app.quit();
    app.quit();
    app.quit();
    await Promise.resolve();
    expect(cleanup).toHaveBeenCalledTimes(1);
    expect(app.exited).toBe(0);

    gate.resolve();
    await state.running;
    expect(app.exited).toBe(1);
  });

  test("a cleanup that hangs is abandoned after the timeout, loudly", async () => {
    jest.useFakeTimers();
    try {
      const app = fakeApp();
      const logger = { log: () => {}, error: jest.fn() };
      installQuitCleanup({ app, cleanup: () => new Promise(() => {}), timeoutMs: 1000, logger });

      app.quit();
      await jest.advanceTimersByTimeAsync(999);
      expect(app.exited).toBe(0);
      await jest.advanceTimersByTimeAsync(2);
      expect(app.exited).toBe(1);
      expect(logger.error.mock.calls[0][0]).toMatch(/did not finish within 1000 ms/);
    } finally {
      jest.useRealTimers();
    }
  });

  test("a cleanup that throws is logged and the app still quits", async () => {
    const app = fakeApp();
    const logger = { log: () => {}, error: jest.fn() };
    const state = installQuitCleanup({
      app,
      cleanup: () => {
        throw new Error("tray already destroyed");
      },
      logger,
    });

    app.quit();
    await state.running;
    expect(app.exited).toBe(1);
    expect(String(logger.error.mock.calls[0][1])).toMatch(/tray already destroyed/);
  });

  test("the default timeout leaves room for the agents' and backend's own bounded stops", () => {
    // 5 s agent shutdown + 2 s tree grace + 1 s SIGKILL wait run before the backend's 3 s + 1 s.
    expect(CLEANUP_TIMEOUT_MS).toBeGreaterThanOrEqual(12000);
  });
});

describe("a fatal error quits through cleanup", () => {
  let tmpDir;
  let added;

  beforeEach(() => {
    tmpDir = fs.mkdtempSync(path.join(require("os").tmpdir(), "gaia-3498-"));
    added = [];
    const origOn = process.on.bind(process);
    jest.spyOn(process, "on").mockImplementation((event, handler) => {
      added.push({ event, handler });
      return origOn(event, handler);
    });
  });

  afterEach(() => {
    added.forEach(({ event, handler }) => process.removeListener(event, handler));
    jest.restoreAllMocks();
    jest.useRealTimers();
    fs.rmSync(tmpDir, { recursive: true, force: true });
  });

  function setup({ exitGraceMs = 1000 } = {}) {
    jest.useFakeTimers();
    const app = fakeApp();
    app.isReady = () => true;
    const exitSpy = jest.spyOn(process, "exit").mockImplementation(() => {});
    const order = [];
    const gate = deferred();
    const state = installQuitCleanup({
      app,
      cleanup: () => {
        order.push("cleanup");
        return gate.promise;
      },
      timeoutMs: 60000,
      logger: quietLogger,
    });
    installSafetyNet({
      logPath: path.join(tmpDir, "main.log"),
      dialogModule: { showErrorBox: jest.fn(), showMessageBoxSync: jest.fn(() => 0) },
      appModule: app,
      homedirFn: () => tmpDir,
      beforeQuit: () => order.push("beforeQuit"),
      exitGraceMs,
    });
    return { app, exitSpy, order, gate, state };
  }

  test("runs the quit cleanup instead of exiting the process", async () => {
    const { app, exitSpy, order, gate, state } = setup();
    process.emit("uncaughtException", new Error("write EPIPE"));
    await jest.advanceTimersByTimeAsync(0);

    expect(order).toEqual(["beforeQuit", "cleanup"]);
    expect(exitSpy).not.toHaveBeenCalled();
    gate.resolve();
    await state.running;
    expect(app.exited).toBe(1);
  });

  test("forces the exit if the cleanup never finishes", async () => {
    const { exitSpy } = setup({ exitGraceMs: 1000 });
    process.emit("uncaughtException", new Error("boom"));

    await jest.advanceTimersByTimeAsync(999);
    expect(exitSpy).not.toHaveBeenCalled();
    await jest.advanceTimersByTimeAsync(2);
    expect(exitSpy).toHaveBeenCalledWith(1);
  });

  test("a second fatal error during that cleanup neither exits nor starts another quit", async () => {
    const { app, exitSpy, order, gate, state } = setup();
    process.emit("uncaughtException", new Error("first"));
    process.emit("uncaughtException", new Error("EPIPE during cleanup"));
    process.emit("unhandledRejection", new Error("and another"));
    await jest.advanceTimersByTimeAsync(0);

    expect(order).toEqual(["beforeQuit", "cleanup"]);
    expect(app.quit).toHaveBeenCalledTimes(1);
    expect(exitSpy).not.toHaveBeenCalled();
    const log = fs.readFileSync(path.join(tmpDir, "main.log"), "utf8");
    expect(log).toMatch(/FATAL \(while already quitting\).*EPIPE during cleanup/);

    gate.resolve();
    await state.running;
    expect(app.exited).toBe(1);
  });

  test("exits at once, with the reason logged, when app.quit() itself throws", () => {
    jest.useFakeTimers();
    const app = new EventEmitter();
    app.isReady = () => false;
    app.quit = () => {
      throw new Error("quit unavailable");
    };
    const exitSpy = jest.spyOn(process, "exit").mockImplementation(() => {});
    const logPath = path.join(tmpDir, "main.log");
    installSafetyNet({
      logPath,
      dialogModule: { showErrorBox: jest.fn(), showMessageBoxSync: jest.fn() },
      appModule: app,
      homedirFn: () => tmpDir,
    });

    process.emit("uncaughtException", new Error("early"));
    expect(exitSpy).toHaveBeenCalledWith(1);
    expect(fs.readFileSync(logPath, "utf8")).toMatch(/app\.quit\(\) failed.*quit unavailable/s);
  });
});

describe("main.cjs quit wiring", () => {
  const MAIN = fs.readFileSync(
    path.resolve(__dirname, "..", "..", "src", "gaia", "apps", "webui", "main.cjs"),
    "utf8"
  );

  test("will-quit cleanup goes through installQuitCleanup, not an ad-hoc handler", () => {
    expect(MAIN).toContain("installQuitCleanup({ app, cleanup })");
    expect(MAIN).not.toMatch(/app\.on\("will-quit"/);
  });

  test("a quit during startup waits for the backend spawn and stops it", () => {
    const body = MAIN.slice(MAIN.indexOf("async function cleanup()"));
    expect(body.indexOf("shutdownStarted = true")).toBeGreaterThan(-1);
    const awaitStart = body.indexOf("await backendStarting");
    const kill = body.indexOf("portManager.killBackend(proc)");
    expect(awaitStart).toBeGreaterThan(-1);
    expect(kill).toBeGreaterThan(awaitStart);
  });

  test("startup does not go on to open the window once a quit has begun", () => {
    const ready = MAIN.slice(MAIN.indexOf("app.whenReady().then(async () => {"));
    const start = ready.indexOf("backendProcess = await backendStarting;");
    const bail = ready.indexOf("if (shutdownStarted)", start);
    const window = ready.indexOf("createWindow();");
    expect(start).toBeGreaterThan(-1);
    expect(bail).toBeGreaterThan(start);
    expect(window).toBeGreaterThan(bail);
  });

  test("the fatal quit skips the are-you-sure question", () => {
    expect(MAIN).toContain("shouldAsk: () => !fatalQuitting && workCanBeLost()");
    expect(MAIN).toMatch(/beforeQuit: \(\) => \{\s*fatalQuitting = true;/);
  });
});
