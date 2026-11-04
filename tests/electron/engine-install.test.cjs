// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

// The backend install behind EngineInstallScreen: what reaches the window, how
// Try again re-runs it, and the install:* IPC handlers.

"use strict";

const fs = require("fs");
const path = require("path");

const {
  createEngineInstall,
  IPC_PROGRESS_EVENT,
  IPC_STATUS_REQUEST,
  IPC_RETRY_REQUEST,
  IPC_OPEN_LOG_FILE,
  IPC_REVERT_REQUEST,
  IPC_CLOSE_LOCKERS_REQUEST,
} = require("../../src/gaia/apps/webui/services/engine-install.cjs");
const { summarizeHolders } = require("../../src/gaia/apps/webui/services/lock-holders.cjs");

const LOG_PATH = "/mock/home/.gaia/install.log";
const OWN_PID = 500;

function setup({
  ensureBackend = jest.fn(async () => "/mock/gaia"),
  window: win,
  previousVersion = null,
  lockHolders,
  platform = "win32",
} = {}) {
  const handlers = new Map();
  const ipcMain = { handle: (channel, fn) => handlers.set(channel, fn) };
  const installer = {
    ensureBackend,
    getLogPath: () => LOG_PATH,
    readLogTail: jest.fn(() => ["last line"]),
  };
  const shell = { openPath: jest.fn(async () => "") };
  const send = jest.fn();
  const target = win === undefined ? { isDestroyed: () => false, webContents: { send } } : win;
  const onFailed = jest.fn();
  const revert = jest.fn(async () => {});
  const engine = createEngineInstall({
    installer,
    ipcMain,
    shell,
    getWindow: () => target,
    isPackaged: true,
    onFailed,
    previousVersion: () => previousVersion,
    revert,
    ...(lockHolders ? { lockHolders } : {}),
    ownPid: OWN_PID,
    platform,
  });
  const invoke = (channel, ...args) => handlers.get(channel)({}, ...args);
  return { engine, installer, shell, send, onFailed, revert, invoke };
}

const installError = (message, extra = {}) => Object.assign(new Error(message), extra);
const tick = () => new Promise((resolve) => setImmediate(resolve));
const sent = (send) => send.mock.calls.map(([, status]) => status);

beforeEach(() => jest.spyOn(console, "error").mockImplementation(() => {}));
afterEach(() => jest.restoreAllMocks());

describe("a successful install", () => {
  test("streams stage, percent and message to the window", async () => {
    const ensureBackend = jest.fn(async ({ onProgress }) => {
      onProgress("install-package", 40, "Installing");
    });
    const { engine, send } = setup({ ensureBackend });

    await expect(engine.run()).resolves.toBe(true);

    expect(send).toHaveBeenLastCalledWith(
      IPC_PROGRESS_EVENT,
      expect.objectContaining({ phase: "running", stage: "install-package", percent: 40, message: "Installing" })
    );
    expect(ensureBackend).toHaveBeenCalledWith(expect.objectContaining({ isPackaged: true }));
  });

  test("a screen that loads late reads the latest status", async () => {
    const release = { resolve: null };
    const ensureBackend = jest.fn(({ onProgress }) => {
      onProgress("verify", 99, "Verifying");
      return new Promise((resolve) => { release.resolve = resolve; });
    });
    const { engine, invoke } = setup({ ensureBackend });

    const run = engine.run();
    expect(await invoke(IPC_STATUS_REQUEST)).toMatchObject({
      phase: "running",
      stage: "verify",
      percent: 99,
      logPath: LOG_PATH,
    });
    release.resolve();
    await run;
  });

  test("starting() reports the backend stage, with no percent", () => {
    const { engine, send } = setup();

    engine.starting();

    expect(sent(send).at(-1)).toMatchObject({ stage: "start", percent: null });
  });

  test("a window closed during the install doesn't stop it", async () => {
    const ensureBackend = jest.fn(async ({ onProgress }) => onProgress("verify", 99, ""));
    const { engine } = setup({ ensureBackend, window: null });

    await expect(engine.run()).resolves.toBe(true);
  });

  test("a destroyed window is not sent to", async () => {
    const send = jest.fn();
    const { engine } = setup({ window: { isDestroyed: () => true, webContents: { send } } });

    await engine.run();

    expect(send).not.toHaveBeenCalled();
  });
});

describe("a failed install", () => {
  test("shows the error's first line, its reason and the log tail, then surfaces the window", async () => {
    const err = installError("Failed to install amd-gaia (pip exit 1).\npip said:\nresolver noise", {
      stage: "install-package",
      reason: "network",
    });
    const { engine, send, onFailed } = setup({ ensureBackend: jest.fn().mockRejectedValue(err) });

    engine.run();
    await tick();

    expect(sent(send).at(-1)).toMatchObject({
      phase: "failed",
      error: {
        stage: "install-package",
        reason: "network",
        message: "Failed to install amd-gaia (pip exit 1).",
        logTail: ["last line"],
        previousVersion: null,
      },
    });
    expect(onFailed).toHaveBeenCalledWith(err);
    engine.stop();
  });

  test("a log that can't be read still shows the failure screen, saying why", async () => {
    const { engine, installer, send, onFailed } = setup({
      ensureBackend: jest.fn().mockRejectedValue(installError("boom")),
    });
    installer.readLogTail.mockImplementation(() => {
      throw Object.assign(new Error("EBUSY: resource busy or locked"), { code: "EBUSY" });
    });

    engine.run();
    await tick();

    expect(sent(send).at(-1)).toMatchObject({
      phase: "failed",
      error: { logTail: ["The install log could not be read: EBUSY: resource busy or locked"] },
    });
    expect(onFailed).toHaveBeenCalled();
    engine.stop();
  });

  test("the failure is sent before the window is surfaced", async () => {
    const { engine, send, onFailed } = setup({
      ensureBackend: jest.fn().mockRejectedValue(installError("boom")),
    });
    onFailed.mockImplementation(() => {
      expect(sent(send).at(-1).phase).toBe("failed");
    });

    engine.run();
    await tick();

    expect(onFailed).toHaveBeenCalled();
    engine.stop();
  });

  test("waits for the user: nothing retries on its own", async () => {
    const ensureBackend = jest.fn().mockRejectedValue(installError("boom"));
    const { engine } = setup({ ensureBackend });

    engine.run();
    await tick();
    await tick();

    expect(ensureBackend).toHaveBeenCalledTimes(1);
    engine.stop();
  });

  test("Try again re-runs ensureBackend and clears the error", async () => {
    const ensureBackend = jest
      .fn()
      .mockRejectedValueOnce(installError("boom"))
      .mockResolvedValueOnce("/mock/gaia");
    const { engine, invoke, send } = setup({ ensureBackend });

    const run = engine.run();
    await tick();
    expect(invoke(IPC_RETRY_REQUEST)).toBe(true);

    await expect(run).resolves.toBe(true);
    expect(ensureBackend).toHaveBeenCalledTimes(2);
    expect(sent(send).at(-1)).toMatchObject({ phase: "running", error: null });
  });

  test("a retry that fails again waits again, with the new error", async () => {
    const ensureBackend = jest
      .fn()
      .mockRejectedValueOnce(installError("first"))
      .mockRejectedValueOnce(installError("second"));
    const { engine, invoke, send } = setup({ ensureBackend });

    engine.run();
    await tick();
    invoke(IPC_RETRY_REQUEST);
    await tick();

    expect(sent(send).at(-1).error.message).toBe("second");
    engine.stop();
  });

  test("a second Try again while the first runs starts nothing", async () => {
    const ensureBackend = jest
      .fn()
      .mockRejectedValueOnce(installError("boom"))
      .mockReturnValueOnce(new Promise(() => {}));
    const { engine, invoke } = setup({ ensureBackend });

    engine.run();
    await tick();
    expect(invoke(IPC_RETRY_REQUEST)).toBe(true);
    await tick();

    expect(invoke(IPC_RETRY_REQUEST)).toBe(false);
    expect(ensureBackend).toHaveBeenCalledTimes(2);
  });

  test("a quit ends the wait without another install", async () => {
    const ensureBackend = jest.fn().mockRejectedValue(installError("boom"));
    const { engine } = setup({ ensureBackend });

    const run = engine.run();
    await tick();
    engine.stop();

    await expect(run).resolves.toBe(false);
    expect(ensureBackend).toHaveBeenCalledTimes(1);
  });

  test("Try again with no failure waiting does nothing", () => {
    const { invoke } = setup();
    expect(invoke(IPC_RETRY_REQUEST)).toBe(false);
  });
});

describe("install:open-log-file", () => {
  test("opens only the installer log, ignoring a renderer-supplied path", async () => {
    const { invoke, shell } = setup();

    const result = await invoke(IPC_OPEN_LOG_FILE, "/etc/passwd");

    expect(shell.openPath).toHaveBeenCalledTimes(1);
    expect(shell.openPath).toHaveBeenCalledWith(LOG_PATH);
    expect(result).toEqual({ ok: true, message: null });
  });

  test("reports the OS error string when the open fails", async () => {
    const { invoke, shell } = setup();
    shell.openPath.mockResolvedValue("Failed to open path");

    await expect(invoke(IPC_OPEN_LOG_FILE)).resolves.toEqual({ ok: false, message: "Failed to open path" });
  });
});

describe("going back after a failed update", () => {
  const failOnce = () => jest.fn(async () => {
    throw installError("boom", { stage: "install-package" });
  });

  test("the failure names the version an update can go back to", async () => {
    const { engine, send } = setup({ ensureBackend: failOnce(), previousVersion: "0.25.3" });
    void engine.run();
    await new Promise((r) => setImmediate(r));
    expect(send.mock.calls.at(-1)[1].error.previousVersion).toBe("0.25.3");
    engine.stop();
  });

  test("Go back installs the previous version while the failure waits", async () => {
    const { engine, invoke, revert } = setup({ ensureBackend: failOnce(), previousVersion: "0.25.3" });
    void engine.run();
    await new Promise((r) => setImmediate(r));
    await expect(invoke(IPC_REVERT_REQUEST)).resolves.toBe(true);
    expect(revert).toHaveBeenCalledTimes(1);
    engine.stop();
  });

  test("a first install has nothing to go back to", async () => {
    const { engine, invoke, revert } = setup({ ensureBackend: failOnce() });
    void engine.run();
    await new Promise((r) => setImmediate(r));
    await expect(invoke(IPC_REVERT_REQUEST)).resolves.toBe(false);
    expect(revert).not.toHaveBeenCalled();
    engine.stop();
  });

  test("with no failure waiting, Go back does nothing", async () => {
    const { invoke, revert } = setup({ previousVersion: "0.25.3" });
    await expect(invoke(IPC_REVERT_REQUEST)).resolves.toBe(false);
    expect(revert).not.toHaveBeenCalled();
  });
});

describe("a file another program holds open", () => {
  const LOCKED_FILE = "C:\\Users\\you\\.gaia\\venv\\Scripts\\gaia.exe";
  const DEMO_GAIA = "C:\\Users\\you\\demo-home\\.gaia\\venv\\Scripts\\gaia.exe";
  const CODE = "C:\\Program Files\\VS Code\\Code.exe";
  const raw = (pid, p, ancestors = []) => ({ pid, name: p.split("\\").pop(), path: p, commandLine: p, ancestors });
  const lockedError = () =>
    installError("GAIA couldn't replace its own files because another program has them open (gaia.exe).", {
      stage: "install-package",
      reason: "locked",
      lockedFiles: [LOCKED_FILE],
    });

  function lockHoldersReturning(...answers) {
    const queue = [...answers];
    return {
      queryLockHolders: jest.fn(async () => queue.length > 1 ? queue.shift() : queue[0]),
      summarizeHolders,
      endProcessTrees: jest.fn(async () => {}),
    };
  }

  test("the failure names who holds it", async () => {
    const lockHolders = lockHoldersReturning([raw(32852, DEMO_GAIA)]);
    const { engine, send } = setup({ ensureBackend: jest.fn().mockRejectedValue(lockedError()), lockHolders });

    engine.run();
    await tick();

    expect(lockHolders.queryLockHolders).toHaveBeenCalledWith([LOCKED_FILE]);
    expect(sent(send).at(-1).error.lockedBy).toEqual([
      { pid: 32852, name: "gaia.exe", path: DEMO_GAIA, folder: "C:\\Users\\you\\demo-home", isGaia: true },
    ]);
    engine.stop();
  });

  test("Close it and continue ends exactly the GAIA processes shown, then installs again", async () => {
    const lockHolders = lockHoldersReturning([raw(32852, DEMO_GAIA)]);
    const ensureBackend = jest.fn().mockRejectedValueOnce(lockedError()).mockResolvedValueOnce("/mock/gaia");
    const { engine, invoke } = setup({ ensureBackend, lockHolders });

    const done = engine.run();
    await tick();
    expect(lockHolders.endProcessTrees).not.toHaveBeenCalled();

    await expect(invoke(IPC_CLOSE_LOCKERS_REQUEST)).resolves.toBe(true);

    expect(lockHolders.endProcessTrees).toHaveBeenCalledWith([32852]);
    await expect(done).resolves.toBe(true);
    expect(ensureBackend).toHaveBeenCalledTimes(2);
    expect(lockHolders.endProcessTrees.mock.invocationCallOrder[0]).toBeLessThan(
      ensureBackend.mock.invocationCallOrder[1]
    );
  });

  test("a program that isn't GAIA is never closed", async () => {
    const lockHolders = lockHoldersReturning([raw(4100, CODE)]);
    const { engine, send, invoke } = setup({ ensureBackend: jest.fn().mockRejectedValue(lockedError()), lockHolders });

    engine.run();
    await tick();

    expect(sent(send).at(-1).error.lockedBy).toEqual([expect.objectContaining({ pid: 4100, isGaia: false })]);
    await expect(invoke(IPC_CLOSE_LOCKERS_REQUEST)).resolves.toBe(false);
    expect(lockHolders.endProcessTrees).not.toHaveBeenCalled();
    engine.stop();
  });

  test("a pid that now belongs to a different program is left alone", async () => {
    const lockHolders = lockHoldersReturning([raw(32852, DEMO_GAIA)], [raw(32852, CODE)]);
    const ensureBackend = jest.fn().mockRejectedValueOnce(lockedError()).mockResolvedValueOnce("/mock/gaia");
    const { engine, invoke } = setup({ ensureBackend, lockHolders });

    const done = engine.run();
    await tick();
    await invoke(IPC_CLOSE_LOCKERS_REQUEST);

    expect(lockHolders.endProcessTrees).toHaveBeenCalledWith([]);
    await done;
  });

  test("a close that fails keeps the failure on screen and doesn't install", async () => {
    const lockHolders = lockHoldersReturning([raw(32852, DEMO_GAIA)]);
    lockHolders.endProcessTrees.mockRejectedValue(new Error("Could not end process 32852: Access is denied."));
    const ensureBackend = jest.fn().mockRejectedValue(lockedError());
    const { engine, invoke } = setup({ ensureBackend, lockHolders });

    engine.run();
    await tick();

    await expect(invoke(IPC_CLOSE_LOCKERS_REQUEST)).rejects.toThrow("Access is denied");
    expect(ensureBackend).toHaveBeenCalledTimes(1);
    expect(invoke(IPC_RETRY_REQUEST)).toBe(true);
    engine.stop();
  });

  test("a lookup that fails leaves lockedBy empty and is logged", async () => {
    const lockHolders = lockHoldersReturning([]);
    lockHolders.queryLockHolders.mockRejectedValue(new Error("powershell timed out"));
    const { engine, send } = setup({ ensureBackend: jest.fn().mockRejectedValue(lockedError()), lockHolders });

    engine.run();
    await tick();

    expect(sent(send).at(-1).error).toMatchObject({ reason: "locked", lockedBy: null });
    expect(console.error).toHaveBeenCalledWith(expect.stringContaining("powershell timed out"));
    engine.stop();
  });

  test("off Windows nothing is looked up", async () => {
    const lockHolders = lockHoldersReturning([]);
    const { engine, send } = setup({
      ensureBackend: jest.fn().mockRejectedValue(lockedError()),
      lockHolders,
      platform: "linux",
    });

    engine.run();
    await tick();

    expect(lockHolders.queryLockHolders).not.toHaveBeenCalled();
    expect(sent(send).at(-1).error.lockedBy).toBeNull();
    engine.stop();
  });
});

describe("preload bridge", () => {
  const preload = fs.readFileSync(
    path.resolve(__dirname, "../../src/gaia/apps/webui/preload.cjs"),
    "utf8"
  );

  test.each([
    ["onProgress", `onEvent("${IPC_PROGRESS_EVENT}", cb)`],
    ["status", `ipcRenderer.invoke("${IPC_STATUS_REQUEST}")`],
    ["retry", `ipcRenderer.invoke("${IPC_RETRY_REQUEST}")`],
    ["openLogFile", `ipcRenderer.invoke("${IPC_OPEN_LOG_FILE}")`],
    ["closeLockingPrograms", `ipcRenderer.invoke("${IPC_CLOSE_LOCKERS_REQUEST}")`],
  ])("gaiaInstall.%s uses the channel main handles", (name, call) => {
    expect(preload).toContain(`${name}: `);
    expect(preload).toContain(call);
  });
});
