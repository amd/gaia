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

test("Keep running puts the restart off; the update installs on the next quit", async () => {
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
