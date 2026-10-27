// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * Tests for auto-updater version-rollback additions (issue #1336).
 *
 * Covers:
 *   - listReleases(): parses, filters, maps and marks isCurrent/isPinned
 *   - listReleases(): surfaces a structured error on fetch failure
 *   - installVersion(): setFeedURL with generic/tag path, pin persist, checkForUpdates
 *   - pin gating: init() sets autoDownload=false when a pin is present
 *   - clearPin(): autoDownload back to the autoUpdate setting, allowDowngrade=false, forward feed
 *   - autoUpdate: off by default; Update now downloads; restart asks first
 *   - getState() includes currentVersion and pinnedVersion
 *   - IPC handlers: invoked with bad input, they reject without side effects
 *
 * electron-updater is mapped via moduleNameMapper in package.json to
 * mocks/electron-updater.js — a module-level singleton MockAutoUpdater.
 * Tests reset its state in beforeEach.
 */

"use strict";

const os = require("os");
const path = require("path");
const fs = require("fs");

const electronMock = require("electron");

// init() requires the restart check main.cjs passes; these tests never reach it.
const INIT_OPTIONS = { confirmRestart: async () => true };

// The mock is provided by moduleNameMapper → mocks/electron-updater.js.
// Grab the singleton so we can inspect/reset it per test.
const { autoUpdater: mockAutoUpdaterInstance } = require("electron-updater");

// ── Helpers ──────────────────────────────────────────────────────────────────

let tmpHome;

beforeEach(() => {
  tmpHome = fs.mkdtempSync(path.join(os.tmpdir(), "gaia-test-"));
  jest.spyOn(os, "homedir").mockReturnValue(tmpHome);
  jest.clearAllMocks();
  mockAutoUpdaterInstance.removeAllListeners();
  mockAutoUpdaterInstance.autoDownload = true;
  mockAutoUpdaterInstance.allowDowngrade = false;
  mockAutoUpdaterInstance._feedURL = null;
  delete process.env.GAIA_DISABLE_UPDATE;
  delete process.env.GAIA_UPDATE_PRERELEASE;
  delete process.env.GAIA_UPDATE_FEED_URL;
});

afterEach(() => {
  // destroy() clears init()'s check timers — leaked, they keep Jest alive.
  for (const m of loadedModules) {
    try {
      m.destroy();
    } catch {}
  }
  loadedModules = [];
  try {
    fs.rmSync(tmpHome, { recursive: true, force: true });
  } catch {}
});

let loadedModules = [];

function loadModule() {
  let m;
  jest.isolateModules(() => {
    m = require("../../src/gaia/apps/webui/services/auto-updater.cjs");
  });
  loadedModules.push(m);
  return m;
}

function makeMockWindow() {
  const win = new electronMock.BrowserWindow();
  win.isDestroyed = jest.fn(() => false);
  return win;
}

// ── GitHub API fixtures ───────────────────────────────────────────────────────

function makeRelease({
  tag = "v0.21.0",
  version = "0.21.0",
  draft = false,
  prerelease = false,
  platform = "win32",
} = {}) {
  const assetName =
    platform === "win32"
      ? `gaia-agent-ui-${version}-x64-setup.exe`
      : platform === "darwin"
      ? `gaia-agent-ui-${version}-arm64.zip`
      : `gaia-agent-ui-${version}-x86_64.AppImage`;

  return {
    tag_name: tag,
    name: `GAIA ${version}`,
    draft,
    prerelease,
    published_at: "2026-06-01T00:00:00Z",
    html_url: `https://github.com/amd/gaia/releases/tag/${tag}`,
    assets: [
      { name: assetName },
      { name: "latest.yml" },
    ],
  };
}

function stubFetch(releases) {
  global.fetch = jest.fn().mockResolvedValue({
    ok: true,
    status: 200,
    json: async () => releases,
  });
}

// A check that finds a release, the way electron-updater reports one.
async function offerRelease() {
  mockAutoUpdaterInstance.emit("update-available", { version: "0.20.0" });
  return null;
}

function writeUpdateConfig(cfg) {
  const dir = path.join(tmpHome, ".gaia");
  fs.mkdirSync(dir, { recursive: true });
  fs.writeFileSync(path.join(dir, "update-config.json"), JSON.stringify(cfg));
}

function readUpdateConfig() {
  return JSON.parse(
    fs.readFileSync(path.join(tmpHome, ".gaia", "update-config.json"), "utf8")
  );
}

// ── Tests: listReleases ────────────────────────────────────────────────────────

describe("listReleases()", () => {
  // listReleases is a pure async function that uses global fetch and app.getVersion()
  // It works even outside of init() context.

  test("returns releases newest-first with isCurrent marking", async () => {
    const mod = loadModule();

    const currentPlatform =
      process.platform === "darwin"
        ? "darwin"
        : process.platform === "linux"
        ? "linux"
        : "win32";

    // Feed the mock API a known-ordered list (newest first, as the GitHub API
    // returns it). The mapped result must preserve that order index-for-index.
    const orderedVersions = ["0.21.0", "0.20.0", "0.19.0", "0.18.0"];
    const releases = orderedVersions.map((v) =>
      makeRelease({ tag: `v${v}`, version: v, platform: currentPlatform })
    );

    stubFetch(releases);
    electronMock.app.getVersion = jest.fn(() => "0.21.0");

    const result = await mod.listReleases();
    expect(Array.isArray(result)).toBe(true);
    expect(result.length).toBe(orderedVersions.length);

    // Newest-first order preserved index-for-index.
    expect(result.map((r) => r.version)).toEqual(orderedVersions);

    // isCurrent marks only the running version.
    expect(result[0].tag).toBe("v0.21.0");
    expect(result[0].isCurrent).toBe(true);
    expect(result.slice(1).every((r) => r.isCurrent === false)).toBe(true);
  });

  test("filters out draft releases", async () => {
    const mod = loadModule();

    const currentPlatform = process.platform === "darwin" ? "darwin" : process.platform === "linux" ? "linux" : "win32";
    const releases = [
      makeRelease({ tag: "v0.22.0-draft", draft: true, platform: currentPlatform }),
      makeRelease({ tag: "v0.21.0", version: "0.21.0", platform: currentPlatform }),
    ];

    stubFetch(releases);
    electronMock.app.getVersion = jest.fn(() => "0.20.0");

    const result = await mod.listReleases();
    expect(Array.isArray(result)).toBe(true);
    expect(result.some((r) => r.tag === "v0.22.0-draft")).toBe(false);
    expect(result.some((r) => r.tag === "v0.21.0")).toBe(true);
  });

  test("filters out prereleases when not opted in", async () => {
    const mod = loadModule();
    delete process.env.GAIA_UPDATE_PRERELEASE;

    const currentPlatform = process.platform === "darwin" ? "darwin" : process.platform === "linux" ? "linux" : "win32";
    const releases = [
      makeRelease({ tag: "v0.22.0-beta1", prerelease: true, platform: currentPlatform }),
      makeRelease({ tag: "v0.21.0", version: "0.21.0", platform: currentPlatform }),
    ];

    stubFetch(releases);
    electronMock.app.getVersion = jest.fn(() => "0.20.0");

    const result = await mod.listReleases();
    expect(result.some((r) => r.tag === "v0.22.0-beta1")).toBe(false);
    expect(result.some((r) => r.tag === "v0.21.0")).toBe(true);
  });

  test("surfaces a structured error object (not []) on fetch failure", async () => {
    const mod = loadModule();

    global.fetch = jest.fn().mockRejectedValue(new Error("ENOTFOUND"));

    const result = await mod.listReleases();
    // Must NOT return an empty array — must surface the error
    expect(Array.isArray(result)).toBe(false);
    expect(result).toHaveProperty("error");
    expect(typeof result.error).toBe("string");
    expect(result.error.toLowerCase()).toContain("github");
  });

  test("surfaces a structured error on non-200 response", async () => {
    const mod = loadModule();

    global.fetch = jest.fn().mockResolvedValue({
      ok: false,
      status: 403,
    });

    const result = await mod.listReleases();
    expect(Array.isArray(result)).toBe(false);
    expect(result).toHaveProperty("error");
  });

  test("maps release fields correctly", async () => {
    const mod = loadModule();

    const currentPlatform = process.platform === "darwin" ? "darwin" : process.platform === "linux" ? "linux" : "win32";
    const release = makeRelease({ tag: "v0.21.0", version: "0.21.0", platform: currentPlatform });
    stubFetch([release]);
    electronMock.app.getVersion = jest.fn(() => "0.19.0");

    const result = await mod.listReleases();
    expect(Array.isArray(result)).toBe(true);
    const r = result.find((x) => x.tag === "v0.21.0");
    expect(r).toBeDefined();
    expect(r.tag).toBe("v0.21.0");
    expect(r.version).toBeDefined();
    expect(r.date).toBe("2026-06-01T00:00:00Z");
    expect(r.notesUrl).toContain("github.com/amd/gaia/releases");
    expect(r.isCurrent).toBe(false);
    expect(typeof r.isPinned).toBe("boolean");
  });

  test("caps the list to the 10 most-recent installable releases", async () => {
    const mod = loadModule();

    const currentPlatform =
      process.platform === "darwin"
        ? "darwin"
        : process.platform === "linux"
        ? "linux"
        : "win32";

    // 15 installable releases, newest-first (as the GitHub API returns them).
    const versions = Array.from({ length: 15 }, (_, i) => `0.${30 - i}.0`);
    const releases = versions.map((v) =>
      makeRelease({ tag: `v${v}`, version: v, platform: currentPlatform })
    );

    stubFetch(releases);
    electronMock.app.getVersion = jest.fn(() => "0.30.0");

    const result = await mod.listReleases();
    expect(Array.isArray(result)).toBe(true);
    // Display cap: only the 10 newest are returned; older remain reachable
    // via the "browse all on GitHub" link in the picker.
    expect(result.length).toBe(10);
    expect(result.map((r) => r.version)).toEqual(versions.slice(0, 10));
  });
});

// ── Tests: installVersion ──────────────────────────────────────────────────────

describe("installVersion()", () => {
  test("sets allowDowngrade=true, calls setFeedURL with generic provider pointing to tag folder", async () => {
    const mod = loadModule();
    const win = makeMockWindow();
    mod.init(win, INIT_OPTIONS);

    const checkSpy = jest
      .spyOn(mockAutoUpdaterInstance, "checkForUpdates")
      .mockImplementation(offerRelease);

    await mod.installVersion("v0.20.0");

    expect(mockAutoUpdaterInstance.allowDowngrade).toBe(true);
    expect(mockAutoUpdaterInstance._feedURL).not.toBeNull();
    expect(mockAutoUpdaterInstance._feedURL.provider).toBe("generic");
    expect(mockAutoUpdaterInstance._feedURL.url).toContain("/releases/download/v0.20.0/");
    expect(checkSpy).toHaveBeenCalled();
  });

  test("persists the pin to ~/.gaia/update-config.json", async () => {
    const mod = loadModule();
    const win = makeMockWindow();
    mod.init(win, INIT_OPTIONS);

    jest.spyOn(mockAutoUpdaterInstance, "checkForUpdates").mockImplementation(offerRelease);

    await mod.installVersion("v0.20.0");

    const configPath = path.join(tmpHome, ".gaia", "update-config.json");
    expect(fs.existsSync(configPath)).toBe(true);
    const config = JSON.parse(fs.readFileSync(configPath, "utf8"));
    expect(config.pinnedVersion).toBe("v0.20.0");
  });

  test("aborts (throws) and does NOT touch the feed/check when the pin write fails", async () => {
    const mod = loadModule();
    const win = makeMockWindow();
    mod.init(win, INIT_OPTIONS);

    // Reset feed state captured during init() so we can assert it stays put.
    mockAutoUpdaterInstance._feedURL = null;
    mockAutoUpdaterInstance.allowDowngrade = false;

    const checkSpy = jest
      .spyOn(mockAutoUpdaterInstance, "checkForUpdates")
      .mockResolvedValue(null);
    const setFeedSpy = jest.spyOn(mockAutoUpdaterInstance, "setFeedURL");

    // Force the pin write to fail.
    const writeSpy = jest
      .spyOn(fs, "writeFileSync")
      .mockImplementation(() => {
        throw new Error("EACCES: permission denied");
      });

    await expect(mod.installVersion("v0.20.0")).rejects.toThrow(
      /Failed to persist version pin — rollback aborted/
    );

    // The download/feed must NOT have been touched — AC2 stays intact.
    expect(setFeedSpy).not.toHaveBeenCalled();
    expect(checkSpy).not.toHaveBeenCalled();
    expect(mockAutoUpdaterInstance.allowDowngrade).toBe(false);
    expect(mockAutoUpdaterInstance._feedURL).toBeNull();

    writeSpy.mockRestore();
  });

  test("throws a busy error when a check is already in progress", async () => {
    // A configured feed so checkForUpdates() reaches electron-updater without
    // a GitHub lookup.
    process.env.GAIA_UPDATE_FEED_URL = "https://updates.example.com/gaia";
    const mod = loadModule();
    const win = makeMockWindow();
    mod.init(win, INIT_OPTIONS);

    // Hold checkForUpdates open so the module-level guard stays set while we
    // fire a second (rollback) check.
    let release;
    const gate = new Promise((resolve) => {
      release = resolve;
    });
    jest
      .spyOn(mockAutoUpdaterInstance, "checkForUpdates")
      .mockImplementation(() => gate);

    // Start a normal check (does not await) so checkInProgress flips true.
    const firstCheck = mod.checkForUpdates();

    await expect(mod.installVersion("v0.20.0")).rejects.toThrow(
      /already in progress/i
    );

    // Let the first check finish to avoid a dangling promise.
    release(null);
    await firstCheck;
  });
});

// ── Tests: pin gating at init ─────────────────────────────────────────────────

describe("pin gating", () => {
  test("init() sets autoDownload=false when a pin exists in update-config.json", () => {
    // Write a pin file before loading
    const gaiaDirPath = path.join(tmpHome, ".gaia");
    fs.mkdirSync(gaiaDirPath, { recursive: true });
    fs.writeFileSync(
      path.join(gaiaDirPath, "update-config.json"),
      JSON.stringify({ pinnedVersion: "v0.20.0" })
    );

    const mod = loadModule();
    const win = makeMockWindow();
    mod.init(win, INIT_OPTIONS);

    expect(mockAutoUpdaterInstance.autoDownload).toBe(false);
  });

  test("init() leaves autoDownload off by default (auto-update is opt-in)", () => {
    const mod = loadModule();
    mod.init(makeMockWindow(), INIT_OPTIONS);

    expect(mockAutoUpdaterInstance.autoDownload).toBe(false);
    expect(mod.getState().autoUpdate).toBe(false);
  });

  test("init() turns autoDownload on when autoUpdate is saved", () => {
    writeUpdateConfig({ autoUpdate: true });
    const mod = loadModule();
    mod.init(makeMockWindow(), INIT_OPTIONS);

    expect(mockAutoUpdaterInstance.autoDownload).toBe(true);
    expect(mod.getState().autoUpdate).toBe(true);
  });

  test("a pin keeps autoDownload off even with autoUpdate saved", () => {
    writeUpdateConfig({ autoUpdate: true, pinnedVersion: "v0.20.0" });
    const mod = loadModule();
    mod.init(makeMockWindow(), INIT_OPTIONS);

    expect(mockAutoUpdaterInstance.autoDownload).toBe(false);
  });
});

// ── Tests: auto-update preference and "Update now" ────────────────────────────

describe("installVersion with auto-update and failures", () => {
  test("with auto-update on, the exact version downloads once, not twice", async () => {
    writeUpdateConfig({ autoUpdate: true });
    const mod = loadModule();
    mod.init(makeMockWindow(), INIT_OPTIONS);
    let autoDownloadDuringCheck;
    jest.spyOn(mockAutoUpdaterInstance, "checkForUpdates").mockImplementation(async () => {
      autoDownloadDuringCheck = mockAutoUpdaterInstance.autoDownload;
      return offerRelease();
    });
    const downloadSpy = jest.spyOn(mockAutoUpdaterInstance, "downloadUpdate");

    await mod.installVersion("v0.20.0");

    expect(autoDownloadDuringCheck).toBe(false);
    expect(downloadSpy).toHaveBeenCalledTimes(1);
  });

  test("a release with no installer leaves the app unpinned", async () => {
    const mod = loadModule();
    mod.init(makeMockWindow(), INIT_OPTIONS);
    jest.spyOn(mockAutoUpdaterInstance, "checkForUpdates").mockResolvedValue(null);

    await expect(mod.installVersion("v0.20.0")).rejects.toThrow(/no installer.*not paused/s);
    expect(mod.getState().pinnedVersion).toBeNull();
    expect(readUpdateConfig().pinnedVersion).toBeNull();
  });

  test("a failed download keeps the earlier pin, not the new one", async () => {
    writeUpdateConfig({ pinnedVersion: "v0.19.0" });
    const mod = loadModule();
    mod.init(makeMockWindow(), INIT_OPTIONS);
    jest.spyOn(mockAutoUpdaterInstance, "checkForUpdates").mockImplementation(offerRelease);
    jest.spyOn(mockAutoUpdaterInstance, "downloadUpdate").mockRejectedValueOnce(new Error("net down"));

    await expect(mod.installVersion("v0.20.0")).rejects.toThrow(/net down.*Still pinned to v0\.19\.0/s);
    expect(mod.getState().pinnedVersion).toBe("v0.19.0");
    expect(readUpdateConfig().pinnedVersion).toBe("v0.19.0");
    expect(mockAutoUpdaterInstance._feedURL.url).toContain("/download/v0.19.0/");
  });
});

describe("setAutoUpdate / downloadUpdate / restart", () => {
  test("set-auto-update persists the choice and keeps other keys", async () => {
    writeUpdateConfig({ feedUrl: "https://updates.example.com/gaia" });
    const mod = loadModule();
    mod.init(makeMockWindow(), INIT_OPTIONS);

    const state = await electronMock.ipcMain.simulateInvoke("gaia:update:set-auto-update", true);

    expect(state.autoUpdate).toBe(true);
    expect(mockAutoUpdaterInstance.autoDownload).toBe(true);
    expect(readUpdateConfig()).toEqual({
      feedUrl: "https://updates.example.com/gaia",
      autoUpdate: true,
    });
  });

  test("set-auto-update rejects a non-boolean without writing", async () => {
    const mod = loadModule();
    mod.init(makeMockWindow(), INIT_OPTIONS);

    await expect(
      electronMock.ipcMain.simulateInvoke("gaia:update:set-auto-update", "yes")
    ).rejects.toThrow(/true or false/);
    expect(fs.existsSync(pinFilePath())).toBe(false);
  });

  test("with auto-update off, a found update is offered, not downloaded", async () => {
    const mod = loadModule();
    mod.init(makeMockWindow(), INIT_OPTIONS);
    const downloadSpy = jest.spyOn(mockAutoUpdaterInstance, "downloadUpdate");

    mockAutoUpdaterInstance.emit("update-available", { version: "0.26.0" });

    expect(mod.getState()).toMatchObject({ status: "available", version: "0.26.0" });
    expect(downloadSpy).not.toHaveBeenCalled();
  });

  test("download (Update now) downloads the offered update", async () => {
    const mod = loadModule();
    mod.init(makeMockWindow(), INIT_OPTIONS);
    const downloadSpy = jest.spyOn(mockAutoUpdaterInstance, "downloadUpdate");
    mockAutoUpdaterInstance.emit("update-available", { version: "0.26.0" });

    const state = await electronMock.ipcMain.simulateInvoke("gaia:update:download");

    expect(downloadSpy).toHaveBeenCalledTimes(1);
    expect(state.status).toBe("downloading");
  });

  test("download with nothing offered fails loudly", async () => {
    const mod = loadModule();
    mod.init(makeMockWindow(), INIT_OPTIONS);

    await expect(electronMock.ipcMain.simulateInvoke("gaia:update:download")).rejects.toThrow(
      /No update is waiting/
    );
  });

  test("turning auto-update on while an update is offered starts the download", async () => {
    const mod = loadModule();
    mod.init(makeMockWindow(), INIT_OPTIONS);
    const downloadSpy = jest.spyOn(mockAutoUpdaterInstance, "downloadUpdate");
    mockAutoUpdaterInstance.emit("update-available", { version: "0.26.0" });

    await electronMock.ipcMain.simulateInvoke("gaia:update:set-auto-update", true);

    expect(downloadSpy).toHaveBeenCalledTimes(1);
  });

  test("restart with nothing downloaded fails loudly", async () => {
    const mod = loadModule();
    mod.init(makeMockWindow(), INIT_OPTIONS);
    const quitSpy = jest.spyOn(mockAutoUpdaterInstance, "quitAndInstall");

    await expect(electronMock.ipcMain.simulateInvoke("gaia:update:restart")).rejects.toThrow(
      /No downloaded update/
    );
    expect(quitSpy).not.toHaveBeenCalled();
  });

  test("restart installs a downloaded update, and asks about running work first", async () => {
    const confirmRestart = jest.fn().mockResolvedValue(false);
    const mod = loadModule();
    mod.init(makeMockWindow(), { confirmRestart });
    const quitSpy = jest.spyOn(mockAutoUpdaterInstance, "quitAndInstall");
    // "Later" on the native dialog, so only the chip's restart is exercised.
    electronMock.dialog.showMessageBox.mockResolvedValueOnce({ response: 1 });
    mockAutoUpdaterInstance.emit("update-downloaded", { version: "0.26.0" });
    await new Promise((r) => setImmediate(r));
    expect(mod.getState().status).toBe("downloaded");

    await electronMock.ipcMain.simulateInvoke("gaia:update:restart");
    expect(quitSpy).not.toHaveBeenCalled();

    confirmRestart.mockResolvedValue(true);
    await electronMock.ipcMain.simulateInvoke("gaia:update:restart");
    expect(quitSpy).toHaveBeenCalledWith(false, true);
  });
});

describe("release candidates (setIncludePrereleases)", () => {
  const rc = { tag: "v0.27.0-rc1", version: "0.27.0-rc1", prerelease: true, platform: process.platform };
  const final = { tag: "v0.26.0", version: "0.26.0", platform: process.platform };

  test("off by default, and RCs stay out of the version list", async () => {
    const mod = loadModule();
    mod.init(makeMockWindow(), INIT_OPTIONS);
    stubFetch([makeRelease(rc), makeRelease(final)]);

    expect(mod.getState().includePrereleases).toBe(false);
    expect(mockAutoUpdaterInstance.allowPrerelease).toBe(false);
    const tags = (await mod.listReleases()).map((r) => r.tag);
    expect(tags).toEqual(["v0.26.0"]);
  });

  test("turning it on needs no auto-update, saves it, and lets RCs through", async () => {
    const mod = loadModule();
    mod.init(makeMockWindow(), INIT_OPTIONS);
    stubFetch([makeRelease(rc), makeRelease(final)]);

    const state = await electronMock.ipcMain.simulateInvoke(
      "gaia:update:set-include-prereleases",
      true
    );

    expect(state.includePrereleases).toBe(true);
    expect(state.autoUpdate).toBe(false);
    expect(mockAutoUpdaterInstance.allowPrerelease).toBe(true);
    // A channel, not an install trigger: nothing downloads unasked.
    expect(mockAutoUpdaterInstance.autoDownload).toBe(false);
    expect(readUpdateConfig()).toEqual({ includePrereleases: true });
    const tags = (await mod.listReleases()).map((r) => r.tag);
    expect(tags).toEqual(["v0.27.0-rc1", "v0.26.0"]);
  });

  test("turning auto-update off keeps the release-candidate channel", async () => {
    writeUpdateConfig({ autoUpdate: true, includePrereleases: true });
    const mod = loadModule();
    mod.init(makeMockWindow(), INIT_OPTIONS);
    expect(mockAutoUpdaterInstance.allowPrerelease).toBe(true);

    await electronMock.ipcMain.simulateInvoke("gaia:update:set-auto-update", false);

    expect(mockAutoUpdaterInstance.autoDownload).toBe(false);
    expect(mockAutoUpdaterInstance.allowPrerelease).toBe(true);
    expect(readUpdateConfig().includePrereleases).toBe(true);
  });

  test("a release-candidate build lists candidates without opting in", async () => {
    jest.spyOn(electronMock.app, "getVersion").mockReturnValue("0.26.1-rc.2");
    const mod = loadModule();
    mod.init(makeMockWindow(), INIT_OPTIONS);
    stubFetch([makeRelease(rc), makeRelease(final)]);

    expect(mod.getState().includePrereleases).toBe(false);
    expect(mockAutoUpdaterInstance.allowPrerelease).toBe(true);
    const tags = (await mod.listReleases()).map((r) => r.tag);
    expect(tags).toEqual(["v0.27.0-rc1", "v0.26.0"]);
  });

  test("a non-boolean is refused without writing", async () => {
    const mod = loadModule();
    mod.init(makeMockWindow(), INIT_OPTIONS);

    await expect(
      electronMock.ipcMain.simulateInvoke("gaia:update:set-include-prereleases", "yes")
    ).rejects.toThrow(/expects true or false/);
    expect(fs.existsSync(pinFilePath())).toBe(false);
  });
});

// ── Tests: clearPin ────────────────────────────────────────────────────────────

describe("clearPin()", () => {
  test("returns autoDownload to the auto-update setting (off), allowDowngrade=false, restores the R2 forward feed, clears pin file", () => {
    // A configured R2 forward feed is what resume restores to (#1724).
    process.env.GAIA_UPDATE_FEED_URL = "https://updates.example.com/gaia";

    // Write a pin
    const gaiaDirPath = path.join(tmpHome, ".gaia");
    fs.mkdirSync(gaiaDirPath, { recursive: true });
    fs.writeFileSync(
      path.join(gaiaDirPath, "update-config.json"),
      JSON.stringify({ pinnedVersion: "v0.20.0" })
    );

    const mod = loadModule();
    const win = makeMockWindow();
    mod.init(win, INIT_OPTIONS);

    // Confirm pin is active
    expect(mockAutoUpdaterInstance.autoDownload).toBe(false);

    mod.clearPin();

    expect(mockAutoUpdaterInstance.autoDownload).toBe(false);
    expect(mockAutoUpdaterInstance.allowDowngrade).toBe(false);
    // Feed should be restored to the generic R2 forward channel.
    expect(mockAutoUpdaterInstance._feedURL).not.toBeNull();
    expect(mockAutoUpdaterInstance._feedURL.provider).toBe("generic");
    expect(mockAutoUpdaterInstance._feedURL.url).toBe(
      "https://updates.example.com/gaia"
    );
    expect(mockAutoUpdaterInstance._feedURL.channel).toBe("latest");

    // Pin file should be cleared (feedUrl, if any, preserved).
    const configPath = path.join(gaiaDirPath, "update-config.json");
    const config = JSON.parse(fs.readFileSync(configPath, "utf8"));
    expect(config.pinnedVersion).toBeNull();
  });
});

// ── Tests: getState additions ──────────────────────────────────────────────────

describe("getState() extended state", () => {
  test("includes currentVersion and pinnedVersion fields", () => {
    const mod = loadModule();
    const win = makeMockWindow();
    mod.init(win, INIT_OPTIONS);

    const state = mod.getState();
    expect(state).toHaveProperty("currentVersion");
    expect(state).toHaveProperty("pinnedVersion");
  });

  test("pinnedVersion reflects a persisted pin", () => {
    const gaiaDirPath = path.join(tmpHome, ".gaia");
    fs.mkdirSync(gaiaDirPath, { recursive: true });
    fs.writeFileSync(
      path.join(gaiaDirPath, "update-config.json"),
      JSON.stringify({ pinnedVersion: "v0.20.0" })
    );

    const mod = loadModule();
    const win = makeMockWindow();
    mod.init(win, INIT_OPTIONS);

    const state = mod.getState();
    expect(state.pinnedVersion).toBe("v0.20.0");
  });
});

// ── Tests: IPC handler registration ───────────────────────────────────────────

describe("IPC handler registration", () => {
  test("removes every update IPC handler on destroy", () => {
    const mod = loadModule();
    const win = makeMockWindow();
    mod.init(win, INIT_OPTIONS);
    mod.destroy();

    expect(electronMock.ipcMain._handlers.has("gaia:update:get-status")).toBe(false);
    expect(electronMock.ipcMain._handlers.has("gaia:update:check")).toBe(false);
    expect(electronMock.ipcMain._handlers.has("gaia:update:list-releases")).toBe(false);
    expect(electronMock.ipcMain._handlers.has("gaia:update:install-version")).toBe(false);
    expect(electronMock.ipcMain._handlers.has("gaia:update:resume")).toBe(false);
  });
});

// ── Tests: resume IPC handler ──────────────────────────────────────────────────

describe("gaia:update:resume IPC", () => {
  test("resume handler calls clearPin and returns updated state", async () => {
    const gaiaDirPath = path.join(tmpHome, ".gaia");
    fs.mkdirSync(gaiaDirPath, { recursive: true });
    fs.writeFileSync(
      path.join(gaiaDirPath, "update-config.json"),
      JSON.stringify({ pinnedVersion: "v0.20.0" })
    );

    const mod = loadModule();
    const win = makeMockWindow();
    mod.init(win, INIT_OPTIONS);

    const result = await electronMock.ipcMain.simulateInvoke("gaia:update:resume");
    expect(result).toHaveProperty("pinnedVersion", null);
    expect(mockAutoUpdaterInstance.autoDownload).toBe(false);
  });
});

// ── Tests: IPC handlers invoked from the renderer ─────────────────────────────

function invoke(channel, ...args) {
  return electronMock.ipcMain.simulateInvoke(channel, ...args);
}

function pinFilePath() {
  return path.join(tmpHome, ".gaia", "update-config.json");
}

describe("gaia:update:install-version IPC tag validation", () => {
  let checkSpy;
  let setFeedSpy;

  beforeEach(() => {
    const mod = loadModule();
    mod.init(makeMockWindow(), INIT_OPTIONS);
    mockAutoUpdaterInstance._feedURL = null;
    mockAutoUpdaterInstance.allowDowngrade = false;
    checkSpy = jest
      .spyOn(mockAutoUpdaterInstance, "checkForUpdates")
      .mockImplementation(offerRelease);
    setFeedSpy = jest.spyOn(mockAutoUpdaterInstance, "setFeedURL");
  });

  test.each([
    ["path traversal", "v0.20.0/../../../evil"],
    ["leading traversal", "../v0.20.0"],
    ["missing v prefix", "0.20.0"],
    ["missing patch", "v0.20"],
    ["four components", "v0.20.0.1"],
    ["channel name", "latest"],
    ["trailing newline", "v0.20.0\n"],
    ["trailing space", "v0.20.0 "],
    ["slash in suffix", "v0.20.0-rc/1"],
    ["query string", "v0.20.0?x=1"],
    ["absolute URL", "https://evil.example/v0.20.0"],
    ["empty string", ""],
    ["null", null],
    ["undefined", undefined],
    ["number", 20],
    ["object", { tag: "v0.20.0" }],
    ["array", ["v0.20.0"]],
  ])("rejects a malformed tag (%s) without pinning or touching the feed", async (_label, tag) => {
    await expect(invoke("gaia:update:install-version", tag)).rejects.toThrow(
      /Invalid release tag/
    );

    expect(setFeedSpy).not.toHaveBeenCalled();
    expect(checkSpy).not.toHaveBeenCalled();
    expect(mockAutoUpdaterInstance.allowDowngrade).toBe(false);
    expect(fs.existsSync(pinFilePath())).toBe(false);
    const status = await invoke("gaia:update:get-status");
    expect(status.pinnedVersion).toBeNull();
  });

  test("accepts a prerelease tag and points the feed at that release folder", async () => {
    const state = await invoke("gaia:update:install-version", "v0.21.0-rc.1");

    expect(state.pinnedVersion).toBe("v0.21.0-rc.1");
    expect(mockAutoUpdaterInstance._feedURL).toEqual({
      provider: "generic",
      url: "https://github.com/amd/gaia/releases/download/v0.21.0-rc.1/",
    });
    expect(checkSpy).toHaveBeenCalledTimes(1);
  });

  test("a rejected tag does not clear an existing pin", async () => {
    await invoke("gaia:update:install-version", "v0.20.0");
    await expect(invoke("gaia:update:install-version", "v0.19.0/..")).rejects.toThrow(
      /Invalid release tag/
    );

    const status = await invoke("gaia:update:get-status");
    expect(status.pinnedVersion).toBe("v0.20.0");
    expect(JSON.parse(fs.readFileSync(pinFilePath(), "utf8")).pinnedVersion).toBe(
      "v0.20.0"
    );
  });
});

describe("gaia:update:check IPC", () => {
  test("with no feed configured, says so when no release has an update for this platform", async () => {
    const mod = loadModule();
    mod.init(makeMockWindow(), INIT_OPTIONS);
    const checkSpy = jest.spyOn(mockAutoUpdaterInstance, "checkForUpdates");
    stubFetch([]);

    const state = await invoke("gaia:update:check");

    expect(state.status).toBe("no-channel");
    expect(state.error).toMatch(/download an installer/);
    expect(checkSpy).not.toHaveBeenCalled();
  });

  test("a failing check surfaces the error in the returned state", async () => {
    process.env.GAIA_UPDATE_FEED_URL = "https://updates.example.com/gaia";
    const mod = loadModule();
    mod.init(makeMockWindow(), INIT_OPTIONS);
    jest
      .spyOn(mockAutoUpdaterInstance, "checkForUpdates")
      .mockRejectedValue(new Error("net::ERR_NAME_NOT_RESOLVED"));

    const state = await invoke("gaia:update:check");

    expect(state.status).toBe("error");
    expect(state.error).toMatch(/ERR_NAME_NOT_RESOLVED/);
  });
});

describe("update-config.json problems are reported, not hidden", () => {
  test("an unreadable file is shown as an error and auto-update stays off", () => {
    const dir = path.join(tmpHome, ".gaia");
    fs.mkdirSync(dir, { recursive: true });
    fs.writeFileSync(path.join(dir, "update-config.json"), "{not json");
    const mod = loadModule();
    mod.init(makeMockWindow(), INIT_OPTIONS);

    expect(mockAutoUpdaterInstance.autoDownload).toBe(false);
    expect(mod.getState().autoUpdate).toBe(false);
    expect(mod.getState().configError).toMatch(/update-config\.json cannot be read/);
  });
});
