// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * Mac builds are ad-hoc signed, so Squirrel.Mac rejects every downloaded update
 * (#7061). On macOS the updater must offer a found update as a release-page
 * download and never download, pin or install one itself; Windows and Linux
 * keep the in-app flow.
 */

"use strict";

const os = require("os");
const path = require("path");
const fs = require("fs");

const electronMock = require("electron");
const { autoUpdater: mockAutoUpdater } = require("electron-updater");
const { refuseFetch } = require("./_helpers/github-releases.cjs");

const INIT_OPTIONS = { confirmRestart: async () => true };
const realPlatform = process.platform;

let tmpHome;
let loaded = [];

function setPlatform(value) {
  Object.defineProperty(process, "platform", { value });
}

function loadModule() {
  let m;
  jest.isolateModules(() => {
    m = require("../../src/gaia/apps/webui/services/auto-updater.cjs");
  });
  loaded.push(m);
  return m;
}

function started() {
  const mod = loadModule();
  const win = new electronMock.BrowserWindow();
  win.isDestroyed = jest.fn(() => false);
  mod.init(win, INIT_OPTIONS);
  return mod;
}

function configPath() {
  return path.join(tmpHome, ".gaia", "update-config.json");
}

function writeUpdateConfig(cfg) {
  fs.mkdirSync(path.dirname(configPath()), { recursive: true });
  fs.writeFileSync(configPath(), JSON.stringify(cfg));
}

/** The coded error the renderer receives from a rejected IPC call. */
async function rendererError(channel, ...args) {
  const err = await electronMock.ipcMain.simulateInvoke(channel, ...args).catch((e) => e);
  expect(err).toBeInstanceOf(Error);
  return JSON.parse(err.message.slice(err.message.indexOf("{")));
}

beforeEach(() => {
  tmpHome = fs.mkdtempSync(path.join(os.tmpdir(), "gaia-mac-update-"));
  jest.spyOn(os, "homedir").mockReturnValue(tmpHome);
  jest.clearAllMocks();
  mockAutoUpdater.removeAllListeners();
  mockAutoUpdater.autoDownload = true;
  mockAutoUpdater.autoInstallOnAppQuit = true;
  mockAutoUpdater.allowDowngrade = false;
  mockAutoUpdater._feedURL = null;
  delete process.env.GAIA_DISABLE_UPDATE;
  delete process.env.GAIA_UPDATE_PRERELEASE;
  delete process.env.GAIA_UPDATE_FEED_URL;
  refuseFetch();
});

afterEach(() => {
  setPlatform(realPlatform);
  for (const m of loaded) {
    try {
      m.destroy();
    } catch {}
  }
  loaded = [];
  jest.restoreAllMocks();
  fs.rmSync(tmpHome, { recursive: true, force: true });
});

describe("on macOS an update is a download from its release page", () => {
  beforeEach(() => setPlatform("darwin"));

  test("a found update links to its release page instead of offering an in-app install", () => {
    const mod = started();

    mockAutoUpdater.emit("update-available", { version: "0.26.0" });

    expect(mod.getState()).toMatchObject({
      status: "available",
      version: "0.26.0",
      manualInstall: true,
      downloadUrl: "https://github.com/amd/gaia/releases/tag/v0.26.0",
    });
  });

  test("a release candidate links to its tag, which drops the version's dot", () => {
    const mod = started();

    mockAutoUpdater.emit("update-available", { version: "0.26.0-rc.13" });

    expect(mod.getState().downloadUrl).toBe("https://github.com/amd/gaia/releases/tag/v0.26.0-rc13");
  });

  test("auto-update on still never downloads or installs on quit", () => {
    writeUpdateConfig({ autoUpdate: true });
    const mod = started();

    expect(mod.getState().autoUpdate).toBe(true);
    expect(mockAutoUpdater.autoDownload).toBe(false);
    expect(mockAutoUpdater.autoInstallOnAppQuit).toBe(false);
  });

  test("Update now is refused with the release page, and nothing downloads", async () => {
    const mod = started();
    const download = jest.spyOn(mockAutoUpdater, "downloadUpdate");
    mockAutoUpdater.emit("update-available", { version: "0.26.0" });

    const coded = await rendererError("gaia:update:download");

    expect(coded).toMatchObject({
      code: "manualInstall",
      params: { url: "https://github.com/amd/gaia/releases/tag/v0.26.0" },
    });
    expect(download).not.toHaveBeenCalled();
    expect(mod.getState().status).toBe("available");
    expect(mockAutoUpdater.autoInstallOnAppQuit).toBe(false);
  });

  test("turning auto-update on while an update is offered doesn't download it", async () => {
    const mod = started();
    const download = jest.spyOn(mockAutoUpdater, "downloadUpdate");
    mockAutoUpdater.emit("update-available", { version: "0.26.0" });

    await mod.setAutoUpdate(true);

    expect(download).not.toHaveBeenCalled();
    expect(mockAutoUpdater.autoDownload).toBe(false);
    expect(mod.getState().status).toBe("available");
  });

  test("picking a version is refused before anything is pinned or fetched", async () => {
    const mod = started();
    const check = jest.spyOn(mockAutoUpdater, "checkForUpdates");

    const coded = await rendererError("gaia:update:install-version", "v0.25.4-rc2");

    expect(coded).toMatchObject({
      code: "manualInstall",
      params: { url: "https://github.com/amd/gaia/releases/tag/v0.25.4-rc2" },
    });
    expect(check).not.toHaveBeenCalled();
    expect(mod.getState().pinnedVersion).toBeNull();
    expect(fs.existsSync(configPath())).toBe(false);
  });

  test("a malformed tag is still rejected as malformed, not as a Mac refusal", async () => {
    started();

    await expect(
      electronMock.ipcMain.simulateInvoke("gaia:update:install-version", "../../etc")
    ).rejects.toThrow(/Invalid release tag/);
  });

  test("a withdrawn release clears the download link", () => {
    const mod = started();
    mockAutoUpdater.emit("update-available", { version: "0.26.0" });

    mockAutoUpdater.emit("update-not-available", { version: "0.25.4" });

    expect(mod.getState()).toMatchObject({ status: "idle", downloadUrl: null });
  });
});

describe.each(["win32", "linux"])("on %s the in-app update is unchanged", (platform) => {
  beforeEach(() => setPlatform(platform));

  test("Update now downloads the offered update", async () => {
    const mod = started();
    const download = jest.spyOn(mockAutoUpdater, "downloadUpdate");
    mockAutoUpdater.emit("update-available", { version: "0.26.0" });

    const state = await electronMock.ipcMain.simulateInvoke("gaia:update:download");

    expect(mod.getState().manualInstall).toBe(false);
    expect(download).toHaveBeenCalledTimes(1);
    expect(state.status).toBe("downloading");
    expect(mockAutoUpdater.autoInstallOnAppQuit).toBe(true);
  });

  test("auto-update on downloads by itself", () => {
    writeUpdateConfig({ autoUpdate: true });
    started();

    expect(mockAutoUpdater.autoDownload).toBe(true);
    expect(mockAutoUpdater.autoInstallOnAppQuit).toBe(true);
  });
});
