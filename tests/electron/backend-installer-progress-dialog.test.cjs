// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

// Behavioural tests for the first-run install progress window: its IPC
// handlers, progress buffering, and the failure-dialog button routing.

"use strict";

const { EventEmitter } = require("events");

const INSTALLER_PATH = "../../src/gaia/apps/webui/services/backend-installer.cjs";
const DIALOG_PATH =
  "../../src/gaia/apps/webui/services/backend-installer-progress-dialog.cjs";

const LOG_PATH = "/mock/home/.gaia/install.log";
const STATE_PATH = "/mock/home/.gaia/install-state.json";

jest.mock("../../src/gaia/apps/webui/services/backend-installer.cjs", () => ({
  getState: jest.fn(() => ({ status: "installing", stage: "venv" })),
  getLogPath: jest.fn(() => "/mock/home/.gaia/install.log"),
  getStatePath: jest.fn(() => "/mock/home/.gaia/install-state.json"),
  ensureBackend: jest.fn(),
}));

class FakeWindow extends EventEmitter {
  constructor(options) {
    super();
    this.options = options;
    this._destroyed = false;
    this.webContents = Object.assign(new EventEmitter(), { send: jest.fn() });
    this.loadURL = jest.fn();
    this.setMenuBarVisibility = jest.fn();
    this.show = jest.fn();
  }

  isDestroyed() {
    return this._destroyed;
  }

  destroy() {
    this._destroyed = true;
    this.emit("closed");
  }
}

/** Fresh module registry per test: the dialog module registers IPC once per process. */
function load() {
  jest.resetModules();
  const electron = require("electron");
  electron.ipcMain._handlers.clear();
  electron.BrowserWindow = jest.fn((options) => new FakeWindow(options));
  electron.dialog.showMessageBox = jest.fn();
  electron.shell.openPath = jest.fn(async () => "");
  electron.shell.openExternal = jest.fn(async () => {});
  electron.clipboard.writeText = jest.fn();
  const installer = require(INSTALLER_PATH);
  const dialog = require(DIALOG_PATH);
  return { electron, installer, dialog };
}

function invoke(electron, channel, ...args) {
  return electron.ipcMain.simulateInvoke(channel, ...args);
}

describe("createProgressWindow", () => {
  test("opens an isolated window with the preload bridge and no node integration", () => {
    const { dialog } = load();
    const { window } = dialog.createProgressWindow();

    const prefs = window.options.webPreferences;
    expect(prefs.contextIsolation).toBe(true);
    expect(prefs.nodeIntegration).toBe(false);
    expect(prefs.preload).toMatch(/preload\.cjs$/);
    expect(window.loadURL.mock.calls[0][0]).toMatch(/^data:text\/html/);
  });

  test("registers the install IPC handlers only once across windows", () => {
    const { electron, dialog } = load();
    const handleSpy = jest.spyOn(electron.ipcMain, "handle");

    dialog.createProgressWindow();
    dialog.createProgressWindow();

    expect(handleSpy.mock.calls.map((c) => c[0]).sort()).toEqual([
      dialog.IPC_COPY_LOG_PATH,
      dialog.IPC_OPEN_LOG_FILE,
      dialog.IPC_STATUS_REQUEST,
    ].sort());
  });

  test("buffers progress until the renderer loads, then replays only the latest", () => {
    const { dialog } = load();
    const { window, onProgress } = dialog.createProgressWindow();

    onProgress("download", 10, "Downloading uv");
    onProgress("venv", 40, "Creating venv");
    expect(window.webContents.send).not.toHaveBeenCalled();

    window.webContents.emit("did-finish-load");
    expect(window.webContents.send).toHaveBeenCalledTimes(1);
    expect(window.webContents.send).toHaveBeenCalledWith(dialog.IPC_PROGRESS_EVENT, {
      stage: "venv",
      percent: 40,
      message: "Creating venv",
    });

    onProgress("install", 70, "Installing gaia");
    expect(window.webContents.send).toHaveBeenLastCalledWith(dialog.IPC_PROGRESS_EVENT, {
      stage: "install",
      percent: 70,
      message: "Installing gaia",
    });
  });

  test("progress after close is dropped instead of sent to a destroyed window", () => {
    const { dialog } = load();
    const { window, onProgress, close } = dialog.createProgressWindow();
    window.webContents.emit("did-finish-load");

    close();
    close();
    onProgress("install", 90, "late");

    expect(window.isDestroyed()).toBe(true);
    expect(window.webContents.send).not.toHaveBeenCalled();
  });
});

describe("install:* IPC handlers", () => {
  test("install:status reports the installer's own state and paths", async () => {
    const { electron, dialog } = load();
    dialog.createProgressWindow();

    const result = await invoke(electron, dialog.IPC_STATUS_REQUEST);

    expect(result).toEqual({
      state: { status: "installing", stage: "venv" },
      logPath: LOG_PATH,
      statePath: STATE_PATH,
    });
  });

  test("install:open-log-file opens only the installer log, ignoring a renderer-supplied path", async () => {
    const { electron, dialog } = load();
    dialog.createProgressWindow();

    const result = await invoke(electron, dialog.IPC_OPEN_LOG_FILE, "/etc/passwd");

    expect(electron.shell.openPath).toHaveBeenCalledTimes(1);
    expect(electron.shell.openPath).toHaveBeenCalledWith(LOG_PATH);
    expect(result).toEqual({ ok: true, message: null });
  });

  test("install:open-log-file reports the OS error string when the open fails", async () => {
    const { electron, dialog } = load();
    dialog.createProgressWindow();
    electron.shell.openPath.mockResolvedValue("Failed to open path");

    const result = await invoke(electron, dialog.IPC_OPEN_LOG_FILE);

    expect(result).toEqual({ ok: false, message: "Failed to open path" });
  });

  test("install:open-log-file reports a thrown error", async () => {
    const { electron, dialog } = load();
    dialog.createProgressWindow();
    electron.shell.openPath.mockRejectedValue(new Error("ENOENT"));

    const result = await invoke(electron, dialog.IPC_OPEN_LOG_FILE);

    expect(result).toEqual({ ok: false, message: "ENOENT" });
  });

  test("install:copy-log-path copies only the installer log path", async () => {
    const { electron, dialog } = load();
    dialog.createProgressWindow();

    const result = await invoke(electron, dialog.IPC_COPY_LOG_PATH, "attacker text");

    expect(electron.clipboard.writeText).toHaveBeenCalledWith(LOG_PATH);
    expect(result).toEqual({ ok: true });
  });

  test("install:copy-log-path reports a clipboard failure", async () => {
    const { electron, dialog } = load();
    dialog.createProgressWindow();
    electron.clipboard.writeText.mockImplementation(() => {
      throw new Error("clipboard unavailable");
    });

    const result = await invoke(electron, dialog.IPC_COPY_LOG_PATH);

    expect(result).toEqual({ ok: false, message: "clipboard unavailable" });
  });
});

describe("showFailureDialog", () => {
  const BUTTON = {
    INSTALL_UV: 0,
    RETRY: 1,
    MANUAL: 2,
    COPY_LOG: 3,
    OPEN_LOG: 4,
    QUIT: 5,
  };

  function answer(electron, ...responses) {
    for (const response of responses) {
      electron.dialog.showMessageBox.mockResolvedValueOnce({ response });
    }
  }

  test.each([
    { label: "Retry", response: BUTTON.RETRY, expected: "retry" },
    { label: "Quit", response: BUTTON.QUIT, expected: "quit" },
    { label: "An unknown button index", response: 99, expected: "quit" },
  ])("$label resolves to $expected", async ({ response, expected }) => {
    const { electron, dialog } = load();
    answer(electron, response);

    await expect(dialog.showFailureDialog(null, { message: "boom" })).resolves.toBe(
      expected
    );
  });

  test("names the failed stage and both log locations, and cancels to Quit", async () => {
    const { electron, dialog } = load();
    answer(electron, BUTTON.QUIT);

    await dialog.showFailureDialog(null, {
      message: "uv failed",
      stage: "venv",
      suggestion: "Check your proxy.",
    });

    const opts = electron.dialog.showMessageBox.mock.calls[0][1];
    expect(opts.message).toBe("uv failed");
    expect(opts.detail).toContain("Stage: venv");
    expect(opts.detail).toContain("Check your proxy.");
    expect(opts.detail).toContain(LOG_PATH);
    expect(opts.detail).toContain(STATE_PATH);
    expect(opts.buttons[opts.cancelId]).toBe("Quit");
  });

  test("Manual opens the CLI install docs and resolves to manual", async () => {
    const { electron, dialog } = load();
    answer(electron, BUTTON.MANUAL);

    await expect(dialog.showFailureDialog(null, {})).resolves.toBe("manual");
    expect(electron.shell.openExternal).toHaveBeenCalledWith(
      "https://amd-gaia.ai/docs/quickstart#cli-install"
    );
  });

  test.each([
    ["Copy log path", BUTTON.COPY_LOG, "clipboard"],
    ["Open log file", BUTTON.OPEN_LOG, "openPath"],
  ])("%s acts on the log, then re-shows the dialog", async (_label, button, action) => {
    const { electron, dialog } = load();
    answer(electron, button, BUTTON.RETRY);

    await expect(dialog.showFailureDialog(null, {})).resolves.toBe("retry");

    expect(electron.dialog.showMessageBox).toHaveBeenCalledTimes(2);
    if (action === "clipboard") {
      expect(electron.clipboard.writeText).toHaveBeenCalledWith(LOG_PATH);
    } else {
      expect(electron.shell.openPath).toHaveBeenCalledWith(LOG_PATH);
    }
  });

  test("Install uv runs the packaged install and resolves to retry on success", async () => {
    const { electron, installer, dialog } = load();
    answer(electron, BUTTON.INSTALL_UV);
    installer.ensureBackend.mockResolvedValue("/mock/gaia");

    await expect(dialog.showFailureDialog(null, {})).resolves.toBe("retry");

    expect(installer.ensureBackend).toHaveBeenCalledWith(
      expect.objectContaining({ isPackaged: true, onProgress: expect.any(Function) })
    );
    const progressWindow = electron.BrowserWindow.mock.results[0].value;
    expect(progressWindow.isDestroyed()).toBe(true);
  });

  test("a failed Install uv re-shows the dialog with the new error", async () => {
    const { electron, installer, dialog } = load();
    answer(electron, BUTTON.INSTALL_UV, BUTTON.QUIT);
    const err = Object.assign(new Error("uv download failed"), { stage: "uv" });
    installer.ensureBackend.mockRejectedValue(err);

    await expect(
      dialog.showFailureDialog(null, { message: "first failure" })
    ).resolves.toBe("quit");

    const second = electron.dialog.showMessageBox.mock.calls[1][1];
    expect(second.message).toBe("uv download failed");
    expect(second.detail).toContain("Stage: uv");
    expect(electron.BrowserWindow.mock.results[0].value.isDestroyed()).toBe(true);
  });
});

describe("showPreCheckFailureDialog", () => {
  test.each([
    [0, "retry"],
    [1, "quit"],
  ])("button %i resolves to %p", async (response, expected) => {
    const { electron, dialog } = load();
    electron.dialog.showMessageBox.mockResolvedValueOnce({ response });

    await expect(
      dialog.showPreCheckFailureDialog(null, { title: "Offline", message: "No network" })
    ).resolves.toBe(expected);
  });
});
