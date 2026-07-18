// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * Tests for the R2-primary configurable forward-update feed (issue #1724).
 *
 * Covers:
 *   - init() applies a generic feed (+ mutable "latest" channel) from
 *     GAIA_UPDATE_FEED_URL and from feedUrl in update-config.json
 *   - init() with no feed enters the loud NO_CHANNEL state (no silent no-op)
 *   - checkForUpdates() surfaces NO_CHANNEL rather than checking a dead feed
 *   - checkForUpdates() picks up a feed configured after init()
 *
 * electron-updater is mapped via moduleNameMapper → mocks/electron-updater.js.
 */

"use strict";

const os = require("os");
const path = require("path");
const fs = require("fs");

const electronMock = require("electron");
const { autoUpdater: mockAutoUpdaterInstance } = require("electron-updater");

let tmpHome;
let loadedModules = [];

beforeEach(() => {
  tmpHome = fs.mkdtempSync(path.join(os.tmpdir(), "gaia-feed-test-"));
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

describe("forward feed resolution", () => {
  test("init() applies a generic feed + 'latest' channel from GAIA_UPDATE_FEED_URL", () => {
    process.env.GAIA_UPDATE_FEED_URL = "https://updates.example.com/gaia";
    const mod = loadModule();
    mod.init(makeMockWindow());

    expect(mockAutoUpdaterInstance._feedURL).toEqual({
      provider: "generic",
      url: "https://updates.example.com/gaia",
      channel: "latest",
    });
    // A configured feed is NOT the NO_CHANNEL state.
    expect(mod.getState().status).not.toBe(mod.STATES.NO_CHANNEL);
  });

  test("init() reads feedUrl from ~/.gaia/update-config.json when env is unset", () => {
    const gaiaDir = path.join(tmpHome, ".gaia");
    fs.mkdirSync(gaiaDir, { recursive: true });
    fs.writeFileSync(
      path.join(gaiaDir, "update-config.json"),
      JSON.stringify({ feedUrl: "https://cdn.example.com/channel" })
    );

    const mod = loadModule();
    mod.init(makeMockWindow());

    expect(mockAutoUpdaterInstance._feedURL.provider).toBe("generic");
    expect(mockAutoUpdaterInstance._feedURL.url).toBe(
      "https://cdn.example.com/channel"
    );
  });

  test("env takes precedence over the config file", () => {
    const gaiaDir = path.join(tmpHome, ".gaia");
    fs.mkdirSync(gaiaDir, { recursive: true });
    fs.writeFileSync(
      path.join(gaiaDir, "update-config.json"),
      JSON.stringify({ feedUrl: "https://cdn.example.com/from-config" })
    );
    process.env.GAIA_UPDATE_FEED_URL = "https://updates.example.com/from-env";

    const mod = loadModule();
    mod.init(makeMockWindow());

    expect(mockAutoUpdaterInstance._feedURL.url).toBe(
      "https://updates.example.com/from-env"
    );
  });

  test("init() with no feed enters the loud NO_CHANNEL state and sets no feed", () => {
    const mod = loadModule();
    mod.init(makeMockWindow());

    const state = mod.getState();
    expect(state.status).toBe(mod.STATES.NO_CHANNEL);
    expect(typeof state.error).toBe("string");
    expect(state.error).toMatch(/no update channel configured/i);
    // Must NOT have silently set a feed.
    expect(mockAutoUpdaterInstance._feedURL).toBeNull();
  });

  test("checkForUpdates() surfaces NO_CHANNEL instead of checking a dead feed", async () => {
    const mod = loadModule();
    mod.init(makeMockWindow());

    const checkSpy = jest
      .spyOn(mockAutoUpdaterInstance, "checkForUpdates")
      .mockResolvedValue(null);

    await mod.checkForUpdates();

    expect(mod.getState().status).toBe(mod.STATES.NO_CHANNEL);
    expect(checkSpy).not.toHaveBeenCalled();
  });

  test("checkForUpdates() picks up a feed configured after init()", async () => {
    const mod = loadModule();
    mod.init(makeMockWindow());
    expect(mod.getState().status).toBe(mod.STATES.NO_CHANNEL);

    // Configure a feed after init, then check again.
    process.env.GAIA_UPDATE_FEED_URL = "https://updates.example.com/gaia";
    const checkSpy = jest
      .spyOn(mockAutoUpdaterInstance, "checkForUpdates")
      .mockResolvedValue(null);

    await mod.checkForUpdates();

    expect(mockAutoUpdaterInstance._feedURL).toEqual({
      provider: "generic",
      url: "https://updates.example.com/gaia",
      channel: "latest",
    });
    expect(checkSpy).toHaveBeenCalled();
  });
});
