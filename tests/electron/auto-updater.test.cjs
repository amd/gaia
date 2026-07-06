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
 *   - clearPin(): restores autoDownload, allowDowngrade=false, github feed
 *   - getState() includes currentVersion and pinnedVersion
 *   - 3 new IPC channels registered on init and removed on destroy
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
    mod.init(win);

    const checkSpy = jest
      .spyOn(mockAutoUpdaterInstance, "checkForUpdates")
      .mockResolvedValue(null);

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
    mod.init(win);

    jest.spyOn(mockAutoUpdaterInstance, "checkForUpdates").mockResolvedValue(null);

    await mod.installVersion("v0.20.0");

    const configPath = path.join(tmpHome, ".gaia", "update-config.json");
    expect(fs.existsSync(configPath)).toBe(true);
    const config = JSON.parse(fs.readFileSync(configPath, "utf8"));
    expect(config.pinnedVersion).toBe("v0.20.0");
  });

  test("aborts (throws) and does NOT touch the feed/check when the pin write fails", async () => {
    const mod = loadModule();
    const win = makeMockWindow();
    mod.init(win);

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
    const mod = loadModule();
    const win = makeMockWindow();
    mod.init(win);

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
    mod.init(win);

    expect(mockAutoUpdaterInstance.autoDownload).toBe(false);
  });

  test("init() sets autoDownload=true when no pin exists", () => {
    const mod = loadModule();
    const win = makeMockWindow();
    mod.init(win);

    expect(mockAutoUpdaterInstance.autoDownload).toBe(true);
  });
});

// ── Tests: clearPin ────────────────────────────────────────────────────────────

describe("clearPin()", () => {
  test("sets autoDownload=true, allowDowngrade=false, restores github feed, clears pin file", () => {
    // Write a pin
    const gaiaDirPath = path.join(tmpHome, ".gaia");
    fs.mkdirSync(gaiaDirPath, { recursive: true });
    fs.writeFileSync(
      path.join(gaiaDirPath, "update-config.json"),
      JSON.stringify({ pinnedVersion: "v0.20.0" })
    );

    const mod = loadModule();
    const win = makeMockWindow();
    mod.init(win);

    // Confirm pin is active
    expect(mockAutoUpdaterInstance.autoDownload).toBe(false);

    mod.clearPin();

    expect(mockAutoUpdaterInstance.autoDownload).toBe(true);
    expect(mockAutoUpdaterInstance.allowDowngrade).toBe(false);
    // Feed should be restored to github
    expect(mockAutoUpdaterInstance._feedURL).not.toBeNull();
    expect(mockAutoUpdaterInstance._feedURL.provider).toBe("github");

    // Pin file should be cleared
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
    mod.init(win);

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
    mod.init(win);

    const state = mod.getState();
    expect(state.pinnedVersion).toBe("v0.20.0");
  });
});

// ── Tests: IPC handler registration ───────────────────────────────────────────

describe("IPC handler registration", () => {
  test("registers gaia:update:list-releases on init", () => {
    const mod = loadModule();
    const win = makeMockWindow();
    mod.init(win);

    expect(electronMock.ipcMain._handlers.has("gaia:update:list-releases")).toBe(true);
  });

  test("registers gaia:update:install-version on init", () => {
    const mod = loadModule();
    const win = makeMockWindow();
    mod.init(win);

    expect(electronMock.ipcMain._handlers.has("gaia:update:install-version")).toBe(true);
  });

  test("registers gaia:update:resume on init", () => {
    const mod = loadModule();
    const win = makeMockWindow();
    mod.init(win);

    expect(electronMock.ipcMain._handlers.has("gaia:update:resume")).toBe(true);
  });

  test("removes all 5 IPC handlers on destroy", () => {
    const mod = loadModule();
    const win = makeMockWindow();
    mod.init(win);
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
    mod.init(win);

    const result = await electronMock.ipcMain.simulateInvoke("gaia:update:resume");
    expect(result).toHaveProperty("pinnedVersion", null);
    expect(mockAutoUpdaterInstance.autoDownload).toBe(true);
  });
});
