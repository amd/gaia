// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * Tests for the forward-update feed: a configured one (R2, issue #1724), or
 * GitHub releases when none is configured.
 *
 * Covers:
 *   - init() applies a generic feed (+ mutable "latest" channel) from
 *     GAIA_UPDATE_FEED_URL and from feedUrl in update-config.json
 *   - with no feed configured, each check reads GitHub releases and points the
 *     feed at the newest one on this install's channel (finals, or candidates
 *     when opted in or running one), and says so when none fits this platform
 *   - checkForUpdates() picks up a feed configured after init()
 *
 * electron-updater is mapped via moduleNameMapper → mocks/electron-updater.js.
 */

"use strict";

const os = require("os");
const path = require("path");
const fs = require("fs");

const electronMock = require("electron");

// init() requires the restart check main.cjs passes; these tests never reach it.
const INIT_OPTIONS = { confirmRestart: async () => true };
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
    mod.init(makeMockWindow(), INIT_OPTIONS);

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
    mod.init(makeMockWindow(), INIT_OPTIONS);

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
    mod.init(makeMockWindow(), INIT_OPTIONS);

    expect(mockAutoUpdaterInstance._feedURL.url).toBe(
      "https://updates.example.com/from-env"
    );
  });

  test("init() with no feed sets none yet, and still schedules the first check", () => {
    jest.useFakeTimers();
    try {
      const mod = loadModule();
      mod.init(makeMockWindow(), INIT_OPTIONS);

      expect(mod.getState().status).not.toBe(mod.STATES.NO_CHANNEL);
      // The GitHub release is resolved per check, not at launch.
      expect(mockAutoUpdaterInstance._feedURL).toBeNull();
      expect(jest.getTimerCount()).toBe(1);
    } finally {
      jest.useRealTimers();
    }
  });

  test("checkForUpdates() picks up a feed configured after init()", async () => {
    const mod = loadModule();
    mod.init(makeMockWindow(), INIT_OPTIONS);

    process.env.GAIA_UPDATE_FEED_URL = "https://updates.example.com/gaia";
    global.fetch = jest.fn();
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
    // A configured feed never consults GitHub.
    expect(global.fetch).not.toHaveBeenCalled();
  });
});

// ── No configured feed: the newest GitHub release on this install's channel ──

const MANIFEST =
  process.platform === "win32"
    ? "latest.yml"
    : process.platform === "darwin"
    ? "latest-mac.yml"
    : "latest-linux.yml";

function installerFor(version) {
  if (process.platform === "win32") return `gaia-agent-ui-${version}-x64-setup.exe`;
  if (process.platform === "darwin") return `gaia-agent-ui-${version}-arm64.zip`;
  return `gaia-agent-ui-${version}-x86_64.AppImage`;
}

// A release shaped like the GitHub API's, e.g. v0.25.2-rc3 with its manifest.
function release(tag, { prerelease = /-rc/.test(tag), draft = false, manifest = true, installer = true } = {}) {
  const version = tag.slice(1).replace(/-rc(\d+)$/, "-rc.$1");
  const assets = [];
  if (installer) assets.push({ name: installerFor(version) });
  if (manifest) assets.push({ name: MANIFEST });
  return { tag_name: tag, prerelease, draft, assets };
}

function serveReleases(releases) {
  global.fetch = jest.fn().mockResolvedValue({ ok: true, status: 200, json: async () => releases });
}

function feedTag() {
  const url = mockAutoUpdaterInstance._feedURL && mockAutoUpdaterInstance._feedURL.url;
  const m = /\/releases\/download\/([^/]+)\/$/.exec(url || "");
  return m ? m[1] : null;
}

function writeConfig(cfg) {
  const dir = path.join(tmpHome, ".gaia");
  fs.mkdirSync(dir, { recursive: true });
  fs.writeFileSync(path.join(dir, "update-config.json"), JSON.stringify(cfg));
}

describe("GitHub releases as the default feed", () => {
  let checkSpy;

  function start(version) {
    jest.spyOn(electronMock.app, "getVersion").mockReturnValue(version);
    const mod = loadModule();
    mod.init(makeMockWindow(), INIT_OPTIONS);
    checkSpy = jest.spyOn(mockAutoUpdaterInstance, "checkForUpdates").mockResolvedValue(null);
    return mod;
  }

  // The live list around v0.25.2-rc3, newest first as GitHub returns it.
  const LIVE = [
    release("v0.25.2-rc3"),
    release("v0.25.2-rc2"),
    release("v0.25.1"),
    release("v0.25.1-rc11"),
  ];

  test("a final install is pointed at the newest final release", async () => {
    const mod = start("0.25.0");
    serveReleases(LIVE);

    await mod.checkForUpdates();

    expect(global.fetch).toHaveBeenCalledWith(
      expect.stringContaining("api.github.com/repos/amd/gaia/releases"),
      expect.anything()
    );
    expect(mockAutoUpdaterInstance._feedURL).toEqual({
      provider: "generic",
      url: "https://github.com/amd/gaia/releases/download/v0.25.1/",
      channel: "latest",
    });
    expect(checkSpy).toHaveBeenCalledTimes(1);
    // Off by default: finding an update never downloads it unasked.
    expect(mockAutoUpdaterInstance.autoDownload).toBe(false);
  });

  test("opting into release candidates points it at the newest candidate", async () => {
    writeConfig({ includePrereleases: true });
    const mod = start("0.25.1");
    serveReleases(LIVE);

    await mod.checkForUpdates();

    expect(feedTag()).toBe("v0.25.2-rc3");
    expect(mod.getState().autoUpdate).toBe(false);
    expect(mockAutoUpdaterInstance.autoDownload).toBe(false);
  });

  test("a release-candidate build is offered candidates without opting in", async () => {
    const mod = start("0.25.2-rc.2");
    serveReleases(LIVE);

    await mod.checkForUpdates();

    expect(feedTag()).toBe("v0.25.2-rc3");
    expect(mockAutoUpdaterInstance.allowPrerelease).toBe(true);
  });

  test("an RC tester whose final shipped is pointed at the final", async () => {
    const mod = start("0.25.2-rc.3");
    serveReleases([release("v0.25.2-rc3"), release("v0.25.2")]);

    await mod.checkForUpdates();

    expect(feedTag()).toBe("v0.25.2");
  });

  test("orders candidates by number, not by text or list position", async () => {
    writeConfig({ includePrereleases: true });
    const mod = start("0.25.1-rc.8");
    serveReleases([release("v0.25.1-rc9"), release("v0.25.1-rc10"), release("v0.25.0")]);

    await mod.checkForUpdates();

    expect(feedTag()).toBe("v0.25.1-rc10");
  });

  test("skips drafts, malformed tags, and releases without this platform's update", async () => {
    writeConfig({ includePrereleases: true });
    const mod = start("0.25.0");
    serveReleases([
      release("v0.26.0", { draft: true }),
      release("v0.25.9", { manifest: false }),
      release("v0.25.8", { installer: false }),
      { tag_name: "nightly", prerelease: true, draft: false, assets: [{ name: MANIFEST }] },
      release("v0.25.1"),
    ]);

    await mod.checkForUpdates();

    expect(feedTag()).toBe("v0.25.1");
  });

  test("no release with an update for this platform says so, never 'up to date'", async () => {
    const mod = start("0.25.0");
    serveReleases([release("v0.25.1", { manifest: false })]);

    await mod.checkForUpdates();

    const state = mod.getState();
    expect(state.status).toBe(mod.STATES.NO_CHANNEL);
    expect(state.error).toMatch(/No GAIA release publishes a desktop update for this platform/);
    expect(state.error).toMatch(/github\.com\/amd\/gaia\/releases/);
    expect(checkSpy).not.toHaveBeenCalled();
  });

  test("a rate-limited GitHub is an error that says what to do", async () => {
    const mod = start("0.25.0");
    global.fetch = jest.fn().mockResolvedValue({ ok: false, status: 403, json: async () => ({}) });

    await mod.checkForUpdates();

    const state = mod.getState();
    expect(state.status).toBe(mod.STATES.ERROR);
    expect(state.error).toMatch(/HTTP 403/);
    expect(state.error).toMatch(/hourly limit/);
    expect(checkSpy).not.toHaveBeenCalled();
  });

  test("an unreachable GitHub is an error, not 'up to date'", async () => {
    const mod = start("0.25.0");
    global.fetch = jest.fn().mockRejectedValue(new Error("getaddrinfo ENOTFOUND api.github.com"));

    await mod.checkForUpdates();

    const state = mod.getState();
    expect(state.status).toBe(mod.STATES.ERROR);
    expect(state.error).toMatch(/reach GitHub/);
    expect(state.error).toMatch(/ENOTFOUND/);
    expect(checkSpy).not.toHaveBeenCalled();
  });

  test("a garbled answer from GitHub is an error", async () => {
    const mod = start("0.25.0");
    serveReleases({ message: "Not Found" });

    await mod.checkForUpdates();

    expect(mod.getState().status).toBe(mod.STATES.ERROR);
    expect(checkSpy).not.toHaveBeenCalled();
  });

  test("unparseable JSON says GitHub's answer couldn't be read, not 'check your connection'", async () => {
    const mod = start("0.25.0");
    global.fetch = jest.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => {
        throw new SyntaxError("Unexpected token < in JSON at position 0");
      },
    });

    await mod.checkForUpdates();

    const state = mod.getState();
    expect(state.status).toBe(mod.STATES.ERROR);
    expect(state.error).toMatch(/GitHub sent a response GAIA couldn't read/);
    expect(state.error).not.toMatch(/connection/);
    expect(checkSpy).not.toHaveBeenCalled();
  });

  test("the request is bounded, and a timeout says GitHub didn't answer", async () => {
    const mod = start("0.25.0");
    global.fetch = jest.fn((_url, opts) => {
      expect(opts && opts.signal).toBeInstanceOf(AbortSignal);
      return Promise.reject(Object.assign(new Error("The operation was aborted due to timeout"), { name: "TimeoutError" }));
    });

    await mod.checkForUpdates();

    const state = mod.getState();
    expect(global.fetch).toHaveBeenCalledTimes(1);
    expect(state.status).toBe(mod.STATES.ERROR);
    expect(state.error).toMatch(/GitHub didn't answer within 20 seconds/);
    expect(checkSpy).not.toHaveBeenCalled();
  });

  test("a hung GitHub is cut off after 20 seconds", async () => {
    const mod = start("0.25.0");
    const controller = new AbortController();
    const timeoutSpy = jest.spyOn(AbortSignal, "timeout").mockReturnValue(controller.signal);
    global.fetch = jest.fn(
      (_url, opts) =>
        new Promise((_resolve, reject) => {
          opts.signal.addEventListener("abort", () => reject(opts.signal.reason));
        })
    );
    try {
      const done = mod.checkForUpdates();
      controller.abort(new DOMException("The operation was aborted due to timeout", "TimeoutError"));
      await done;
      expect(timeoutSpy).toHaveBeenCalledWith(20 * 1000);
      expect(mod.getState().error).toMatch(/GitHub didn't answer/);
    } finally {
      timeoutSpy.mockRestore();
    }
  });

  test("each check re-reads GitHub, so a new release is found", async () => {
    const mod = start("0.25.0");
    serveReleases([release("v0.25.1")]);
    await mod.checkForUpdates();
    expect(feedTag()).toBe("v0.25.1");

    serveReleases([release("v0.25.2"), release("v0.25.1")]);
    await mod.checkForUpdates();
    expect(feedTag()).toBe("v0.25.2");
  });

  test("a pinned install keeps its own feed and does not consult the list", async () => {
    writeConfig({ pinnedVersion: "v0.24.0" });
    const mod = start("0.24.0");
    serveReleases(LIVE);

    await mod.checkForUpdates();

    expect(global.fetch).not.toHaveBeenCalled();
    expect(feedTag()).not.toBe("v0.25.1");
  });
});
