// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * "Restart now" in the update dialog asks before it cuts off running work
 * (src/gaia/apps/webui/services/auto-updater.cjs, init's confirmRestart).
 * quitAndInstall starts the installer at once, so the question has to come
 * first; the quit that follows skips the quit guard.
 */

"use strict";

const os = require("os");
const path = require("path");
const fs = require("fs");

const electronMock = require("electron");
const { autoUpdater: mockUpdater } = require("electron-updater");

let tmpHome;
let loaded = [];

beforeEach(() => {
  tmpHome = fs.mkdtempSync(path.join(os.tmpdir(), "gaia-test-"));
  jest.spyOn(os, "homedir").mockReturnValue(tmpHome);
  jest.clearAllMocks();
  mockUpdater.removeAllListeners();
  mockUpdater.quitAndInstall = jest.fn();
  delete process.env.GAIA_DISABLE_UPDATE;
  electronMock.app.getVersion = () => "0.25.0";
});

afterEach(() => {
  for (const m of loaded) m.destroy();
  loaded = [];
  fs.rmSync(tmpHome, { recursive: true, force: true });
});

function initUpdater(options) {
  let m;
  jest.isolateModules(() => {
    m = require("../../src/gaia/apps/webui/services/auto-updater.cjs");
  });
  loaded.push(m);
  const win = new electronMock.BrowserWindow();
  m.init(win, options);
  return m;
}

const flush = () => new Promise((r) => setImmediate(r));

async function downloadedAndRestartChosen() {
  electronMock.dialog.showMessageBox.mockResolvedValueOnce({ response: 0 });
  mockUpdater.emit("update-downloaded", { version: "0.26.0" });
  for (let i = 0; i < 5; i++) await flush();
}

test("restarts when nothing would be lost", async () => {
  const confirmRestart = jest.fn(async () => true);
  initUpdater({ confirmRestart });
  await downloadedAndRestartChosen();
  expect(confirmRestart).toHaveBeenCalledTimes(1);
  expect(mockUpdater.quitAndInstall).toHaveBeenCalledWith(false, true);
});

function saveConfig(cfg) {
  fs.mkdirSync(path.join(tmpHome, ".gaia"), { recursive: true });
  fs.writeFileSync(path.join(tmpHome, ".gaia", "update-config.json"), JSON.stringify(cfg));
}

test("Keep running puts the restart off; the update installs on the next quit", async () => {
  saveConfig({ autoUpdate: true });
  const confirmRestart = jest.fn(async () => false);
  initUpdater({ confirmRestart });
  await downloadedAndRestartChosen();
  expect(confirmRestart).toHaveBeenCalledTimes(1);
  expect(mockUpdater.quitAndInstall).not.toHaveBeenCalled();
  expect(mockUpdater.autoInstallOnAppQuit).toBe(true);
});

test("Later never asks about running work", async () => {
  const confirmRestart = jest.fn(async () => true);
  initUpdater({ confirmRestart });
  electronMock.dialog.showMessageBox.mockResolvedValueOnce({ response: 1 });
  mockUpdater.emit("update-downloaded", { version: "0.26.0" });
  for (let i = 0; i < 5; i++) await flush();
  expect(confirmRestart).not.toHaveBeenCalled();
  expect(mockUpdater.quitAndInstall).not.toHaveBeenCalled();
});

test("a failing restart check is shown to the user, and nothing installs", async () => {
  const confirmRestart = jest.fn(async () => {
    throw new Error("dialog failed");
  });
  initUpdater({ confirmRestart });
  await downloadedAndRestartChosen();
  expect(mockUpdater.quitAndInstall).not.toHaveBeenCalled();
  expect(electronMock.dialog.showErrorBox).toHaveBeenCalledWith(
    "GAIA update",
    expect.stringMatching(/dialog failed[\s\S]*installs the next time you quit/)
  );
});

test.each([[undefined], [{}], [{ confirmRestart: true }]])(
  "init without a restart check (%p) fails loudly instead of restarting silently",
  (options) => {
    expect(() => initUpdater(options)).toThrow(/needs options.confirmRestart/);
  }
);

test("main.cjs hands init the restart check", () => {
  // main.cjs can't be required without launching Electron; init() throwing
  // above is what makes a dropped option fail at startup, this pins main's call.
  const main = fs.readFileSync(
    path.join(__dirname, "..", "..", "src", "gaia", "apps", "webui", "main.cjs"),
    "utf8"
  );
  expect(main).toContain("autoUpdater.init(mainWindow, { confirmRestart: confirmUpdateRestart })");
});

test("turning auto-update off stops an unasked download from installing on quit", async () => {
  saveConfig({ autoUpdate: true });
  const m = initUpdater({ confirmRestart: jest.fn(async () => false) });
  await downloadedAndRestartChosen();
  await m.setAutoUpdate(false);
  expect(mockUpdater.autoInstallOnAppQuit).toBe(false);
  expect(mockUpdater.autoDownload).toBe(false);
});

test("auto-update switched off by the Terminal UI stops the next download", async () => {
  saveConfig({ autoUpdate: true });
  const m = initUpdater({ confirmRestart: jest.fn(async () => true) });
  expect(mockUpdater.autoDownload).toBe(true);
  saveConfig({ autoUpdate: false, feedUrl: "https://example.test/feed/" });
  await m.checkForUpdates();
  expect(mockUpdater.autoDownload).toBe(false);
  expect(mockUpdater.autoInstallOnAppQuit).toBe(false);
  expect(m.getState().autoUpdate).toBe(false);
});

test("an unreadable settings file pauses checks instead of ignoring a pin", async () => {
  const m = initUpdater({ confirmRestart: jest.fn(async () => true) });
  fs.mkdirSync(path.join(tmpHome, ".gaia"), { recursive: true });
  fs.writeFileSync(path.join(tmpHome, ".gaia", "update-config.json"), "{ not json");
  mockUpdater.checkForUpdates = jest.fn(async () => null);
  await m.checkForUpdates();
  expect(mockUpdater.checkForUpdates).not.toHaveBeenCalled();
  expect(m.getState().status).toBe("error");
  expect(m.getState().error).toMatch(/cannot be read/);
});

test("a save waits for the Terminal UI's lock, then fails loudly if it is held", async () => {
  const m = initUpdater({ confirmRestart: jest.fn(async () => true) });
  saveConfig({ tuiPinnedVersion: "v0.24.0" });
  const lock = path.join(tmpHome, ".gaia", "update-config.json.lock");
  fs.writeFileSync(lock, "");
  await expect(m.setAutoUpdate(true)).rejects.toThrow(/busy/);
  const old = new Date(Date.now() - 60000);
  fs.utimesSync(lock, old, old); // a crashed app's lock is taken over
  await m.setAutoUpdate(true);
  const saved = JSON.parse(fs.readFileSync(path.join(tmpHome, ".gaia", "update-config.json"), "utf8"));
  expect(saved).toEqual({ tuiPinnedVersion: "v0.24.0", autoUpdate: true });
  expect(fs.existsSync(lock)).toBe(false);
});

test("an update the user asked for still installs on quit with auto-update off", async () => {
  const m = initUpdater({ confirmRestart: jest.fn(async () => false) });
  expect(mockUpdater.autoInstallOnAppQuit).toBe(false);
  mockUpdater.downloadUpdate = jest.fn(async () => null);
  mockUpdater.emit("update-available", { version: "0.26.0" });
  await flush();
  await m.downloadUpdate();
  expect(mockUpdater.autoInstallOnAppQuit).toBe(true);
  expect(mockUpdater.autoDownload).toBe(false);
});

test("a pin in a file that was broken at launch is honoured once the file is fixed", async () => {
  fs.mkdirSync(path.join(tmpHome, ".gaia"), { recursive: true });
  fs.writeFileSync(path.join(tmpHome, ".gaia", "update-config.json"), "{ half-writ");
  const m = initUpdater({ confirmRestart: jest.fn(async () => true) });
  mockUpdater.checkForUpdates = jest.fn(async () => null);
  global.fetch = jest.fn();
  saveConfig({ autoUpdate: true, pinnedVersion: "v0.24.0" });
  await m.checkForUpdates();
  expect(m.getState().pinnedVersion).toBe("v0.24.0");
  expect(mockUpdater.autoDownload).toBe(false);
  expect(global.fetch).not.toHaveBeenCalled(); // stays on the pinned feed
});

test("auto-update switched off in the Terminal UI after a download stops the install on quit", async () => {
  saveConfig({ autoUpdate: true });
  initUpdater({ confirmRestart: jest.fn(async () => false) });
  await downloadedAndRestartChosen();
  expect(mockUpdater.autoInstallOnAppQuit).toBe(true);
  saveConfig({ autoUpdate: false });
  electronMock.app.emit("before-quit");
  expect(mockUpdater.autoInstallOnAppQuit).toBe(false);
});

describe("a settings folder that refuses the lock file", () => {
  function denyLock(code) {
    const realOpen = fs.openSync;
    return jest.spyOn(fs, "openSync").mockImplementation((p, flags, ...rest) => {
      if (String(p).endsWith(".lock")) {
        const err = new Error(`${code}: permission denied, open '${p}'`);
        err.code = code;
        throw err;
      }
      return realOpen(p, flags, ...rest);
    });
  }

  function onPlatform(value, fn) {
    const platform = Object.getOwnPropertyDescriptor(process, "platform");
    Object.defineProperty(process, "platform", { value });
    return Promise.resolve(fn()).finally(() => Object.defineProperty(process, "platform", platform));
  }

  test("fails at once on Linux, naming permissions, not busy", async () => {
    const m = initUpdater({ confirmRestart: jest.fn(async () => true) });
    const spy = denyLock("EACCES");
    try {
      await onPlatform("linux", async () => {
        const start = Date.now();
        const err = await m.setAutoUpdate(true).catch((e) => e);
        expect(err.message).toMatch(/permissions/);
        expect(err.message).not.toMatch(/busy/);
        expect(Date.now() - start).toBeLessThan(400);
      });
    } finally {
      spy.mockRestore();
    }
  });

  test("on Windows waits for a lock being deleted, then names the real error", async () => {
    const m = initUpdater({ confirmRestart: jest.fn(async () => true) });
    const spy = denyLock("EPERM");
    try {
      await onPlatform("win32", async () => {
        const err = await m.setAutoUpdate(true).catch((e) => e);
        expect(err.message).toMatch(/Cannot lock .*EPERM/);
        expect(err.message).not.toMatch(/busy/);
      });
    } finally {
      spy.mockRestore();
    }
  });
});
