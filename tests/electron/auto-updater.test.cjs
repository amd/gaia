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
 */

"use strict";

const os = require("os");
const path = require("path");
const fs = require("fs");

const electronMock = require("electron");

// Mock electron-updater with EventEmitter-based autoUpdater
const { EventEmitter } = require("events");
class MockAutoUpdater extends EventEmitter {
  constructor() {
    super();
    this.autoDownload = true;
    this.autoInstallOnAppQuit = true;
    this.disableWebInstaller = false;
    this.allowDowngrade = false;
    this.allowPrerelease = false;
    this.logger = null;
    this._feedURL = null;
  }
  setFeedURL(opts) {
    this._feedURL = opts;
  }
  getFeedURL() {
    return this._feedURL;
  }
  async checkForUpdates() {
    return null;
  }
  quitAndInstall() {}
}

const mockAutoUpdaterInstance = new MockAutoUpdater();

jest.mock("electron-updater", () => ({
  autoUpdater: mockAutoUpdaterInstance,
}));

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
  try {
    fs.rmSync(tmpHome, { recursive: true, force: true });
  } catch {}
});

function loadModule() {
  let m;
  jest.isolateModules(() => {
    m = require("../../src/gaia/apps/webui/services/auto-updater.cjs");
  });
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
      { name: `latest.yml` },
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
  test("returns releases newest-first with isCurrent marking", async () => {
    process.env.GAIA_DISABLE_UPDATE = "1";
    const mod = loadModule();

    // Re-enable for this specific test by patching env after load
    delete process.env.GAIA_DISABLE_UPDATE;

    const releases = [
      makeRelease({ tag: "v0.21.0", version: "0.21.0", platform: "win32" }),
      makeRelease({ tag: "v0.20.0", version: "0.20.0", platform: "win32" }),
    ];

    stubFetch(releases);
    // Override app.getVersion for this test
    electronMock.app.getVersion = jest.fn(() => "0.21.0");

    const result = await mod.listReleases();
    expect(result.error).toBeUndefined();
    expect(Array.isArray(result)).toBe(true);
    expect(result).toHaveLength(2);
    expect(result[0].tag).toBe("v0.21.0");
    expect(result[0].isCurrent).toBe(true);
    expect(result[1].tag).toBe("v0.20.0");
    expect(result[1].isCurrent).toBe(false);
  });

  test("filters out draft releases", async () => {
    process.env.GAIA_DISABLE_UPDATE = "1";
    const mod = loadModule();
    delete process.env.GAIA_DISABLE_UPDATE;

    const releases = [
      makeRelease({ tag: "v0.22.0-draft", draft: true, platform: "win32" }),
      makeRelease({ tag: "v0.21.0", version: "0.21.0", platform: "win32" }),
    ];

    stubFetch(releases);
    electronMock.app.getVersion = jest.fn(() => "0.20.0");

    const result = await mod.listReleases();
    expect(result).toHaveLength(1);
    expect(result[0].tag).toBe("v0.21.0");
  });

  test("filters out prereleases when not opted in", async () => {
    process.env.GAIA_DISABLE_UPDATE = "1";
    const mod = loadModule();
    delete process.env.GAIA_DISABLE_UPDATE;

    const releases = [
      makeRelease({ tag: "v0.22.0-beta1", prerelease: true, platform: "win32" }),
      makeRelease({ tag: "v0.21.0", version: "0.21.0", platform: "win32" }),
    ];

    stubFetch(releases);
    electronMock.app.getVersion = jest.fn(() => "0.20.0");

    const result = await mod.listReleases();
    expect(result).toHaveLength(1);
    expect(result[0].tag).toBe("v0.21.0");
  });

  test("surfaces a structured error object (not []) on fetch failure", async () => {
    process.env.GAIA_DISABLE_UPDATE = "1";
    const mod = loadModule();
    delete process.env.GAIA_DISABLE_UPDATE;

    global.fetch = jest.fn().mockRejectedValue(new Error("ENOTFOUND"));

    const result = await mod.listReleases();
    // Must NOT return an empty array — must surface the error
    expect(Array.isArray(result)).toBe(false);
    expect(result).toHaveProperty("error");
    expect(result.error).toContain("GitHub");
  });

  test("surfaces a structured error on non-200 response", async () => {
    process.env.GAIA_DISABLE_UPDATE = "1";
    const mod = loadModule();
    delete process.env.GAIA_DISABLE_UPDATE;

    global.fetch = jest.fn().mockResolvedValue({
      ok: false,
      status: 403,
    });

    const result = await mod.listReleases();
    expect(Array.isArray(result)).toBe(false);
    expect(result).toHaveProperty("error");
  });

  test("maps release fields correctly", async () => {
    process.env.GAIA_DISABLE_UPDATE = "1";
    const mod = loadModule();
    delete process.env.GAIA_DISABLE_UPDATE;

    const release = makeRelease({ tag: "v0.21.0", version: "0.21.0", platform: "win32" });
    stubFetch([release]);
    electronMock.app.getVersion = jest.fn(() => "0.19.0");

    const result = await mod.listReleases();
    const r = result[0];
    expect(r.tag).toBe("v0.21.0");
    expect(r.version).toBeDefined();
    expect(r.date).toBe("2026-06-01T00:00:00Z");
    expect(r.notesUrl).toContain("github.com/amd/gaia/releases");
    expect(r.isCurrent).toBe(false);
    expect(typeof r.isPinned).toBe("boolean");
  });
});

// ── Tests: installVersion ──────────────────────────────────────────────────────

describe("installVersion()", () => {
  test("sets allowDowngrade=true, calls setFeedURL with generic provider pointing to tag folder", async () => {
    process.env.GAIA_DISABLE_UPDATE = "1";
    const mod = loadModule();
    delete process.env.GAIA_DISABLE_UPDATE;

    const checkSpy = jest
      .spyOn(mockAutoUpdaterInstance, "checkForUpdates")
      .mockResolvedValue(null);

    // Need to init first so autoUpdaterRef is populated
    const win = makeMockWindow();
    mod.init(win);

    await mod.installVersion("v0.20.0");

    expect(mockAutoUpdaterInstance.allowDowngrade).toBe(true);
    expect(mockAutoUpdaterInstance._feedURL).not.toBeNull();
    expect(mockAutoUpdaterInstance._feedURL.provider).toBe("generic");
    expect(mockAutoUpdaterInstance._feedURL.url).toContain("/releases/download/v0.20.0/");
    expect(checkSpy).toHaveBeenCalled();
  });

  test("persists the pin to ~/.gaia/update-config.json", async () => {
    process.env.GAIA_DISABLE_UPDATE = "1";
    const mod = loadModule();
    delete process.env.GAIA_DISABLE_UPDATE;

    jest.spyOn(mockAutoUpdaterInstance, "checkForUpdates").mockResolvedValue(null);

    const win = makeMockWindow();
    mod.init(win);

    await mod.installVersion("v0.20.0");

    const configPath = path.join(tmpHome, ".gaia", "update-config.json");
    expect(fs.existsSync(configPath)).toBe(true);
    const config = JSON.parse(fs.readFileSync(configPath, "utf8"));
    expect(config.pinnedVersion).toBe("v0.20.0");
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
