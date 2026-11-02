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
} = require("../../src/gaia/apps/webui/services/engine-install.cjs");

const LOG_PATH = "/mock/home/.gaia/install.log";

function setup({ ensureBackend = jest.fn(async () => "/mock/gaia"), window: win } = {}) {
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
  const engine = createEngineInstall({
    installer,
    ipcMain,
    shell,
    getWindow: () => target,
    isPackaged: true,
    onFailed,
  });
  const invoke = (channel, ...args) => handlers.get(channel)({}, ...args);
  return { engine, installer, shell, send, onFailed, invoke };
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
      },
    });
    expect(onFailed).toHaveBeenCalledWith(err);
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
  ])("gaiaInstall.%s uses the channel main handles", (name, call) => {
    expect(preload).toContain(`${name}: `);
    expect(preload).toContain(call);
  });
});
