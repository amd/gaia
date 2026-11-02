// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * Tests for the forward-update feed: a configured one (R2, issue #1724), or
 * GitHub releases when none is configured.
 *
 * Covers:
 *   - init() applies a generic feed (+ mutable "latest" channel) from
 *     GAIA_UPDATE_FEED_URL and from feedUrl in update-config.json
 *   - with no feed configured, each check reads the releases atom feed plus
 *     latest/download and points the feed at the newest release on this
 *     install's channel (finals, or candidates when opted in or running one)
 *     whose manifest and installer both exist, and says so when none does
 *   - nothing reads api.github.com, whose anonymous limit a network can use up
 *   - a check nobody asked for fails quietly; one the user asked for doesn't
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
const {
  fetchedUrls,
  httpResponse,
  installerName,
  manifestName,
  refuseFetch,
  release,
  serveReleases,
} = require("./_helpers/github-releases.cjs");

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
  refuseFetch();
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
  expect(fetchedUrls().filter((url) => url.includes("api.github.com"))).toEqual([]);
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

  // Runs `fn` as if on another OS; fixtures must be built inside it.
  async function onPlatform(platformValue, archValue, fn) {
    const platform = Object.getOwnPropertyDescriptor(process, "platform");
    const arch = Object.getOwnPropertyDescriptor(process, "arch");
    Object.defineProperty(process, "platform", { value: platformValue });
    Object.defineProperty(process, "arch", { value: archValue });
    try {
      await fn();
    } finally {
      Object.defineProperty(process, "platform", platform);
      Object.defineProperty(process, "arch", arch);
    }
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

    expect(fetchedUrls()).toContain("https://github.com/amd/gaia/releases.atom");
    expect(mockAutoUpdaterInstance._feedURL).toEqual({
      provider: "generic",
      url: "https://github.com/amd/gaia/releases/download/v0.25.1/",
      channel: "latest",
      useMultipleRangeRequest: false,
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

  test("skips drafts, other kinds of tag, and releases without this platform's update", async () => {
    writeConfig({ includePrereleases: true });
    const mod = start("0.25.0");
    serveReleases([
      release("v0.26.0", { draft: true }),
      release("agent-pkg-gaia-v0.3.3"),
      release("v0.25.9", { manifest: false }),
      release("v0.25.8", { installer: false }),
      release("nightly"),
      release("v0.25.1"),
    ]);

    await mod.checkForUpdates();

    expect(feedTag()).toBe("v0.25.1");
    expect(fetchedUrls().filter((url) => url.includes("agent-pkg"))).toEqual([]);
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

  test("arm64 Linux is not pointed at a release that ships only the x86_64 AppImage", async () => {
    await onPlatform("linux", "arm64", async () => {
      const mod = start("0.25.0");
      serveReleases([
        release("v0.25.1", {
          assets: { "gaia-agent-ui-0.25.1-x86_64.AppImage": "", "latest-linux.yml": "version: 0.25.1\n" },
        }),
      ]);
      await mod.checkForUpdates();
      expect(mod.getState().status).toBe(mod.STATES.NO_CHANNEL);
      expect(checkSpy).not.toHaveBeenCalled();
    });
  });

  test("a macOS manifest naming a zip that was never attached is skipped", async () => {
    await onPlatform("darwin", "arm64", async () => {
      const mod = start("0.25.0");
      serveReleases([release("v0.25.1", { installer: false })]);

      await mod.checkForUpdates();

      const state = mod.getState();
      expect(fetchedUrls()).toContain(
        "https://github.com/amd/gaia/releases/download/v0.25.1/gaia-agent-ui-0.25.1-arm64.zip"
      );
      expect(state.status).toBe(mod.STATES.NO_CHANNEL);
      expect(state.error).toMatch(/can't update itself here/);
      expect(state.error).toMatch(/latest-mac\.yml/);
      expect(checkSpy).not.toHaveBeenCalled();
    });
  });

  test.each([
    ["the manifest", { manifest: false }],
    ["the installer", { installer: false }],
  ])("a release still waiting for %s is skipped for the older one", async (_label, uploading) => {
    const mod = start("0.25.0");
    serveReleases([release("v0.25.2", uploading), release("v0.25.1")]);

    await mod.checkForUpdates();

    expect(feedTag()).toBe("v0.25.1");
    expect(checkSpy).toHaveBeenCalledTimes(1);
  });

  test("the installer is probed with HEAD, never downloaded", async () => {
    const mod = start("0.25.0");
    serveReleases([release("v0.25.1")]);

    await mod.checkForUpdates();

    const probe = global.fetch.mock.calls.find(([url]) => url.endsWith(installerName("0.25.1")));
    expect(probe[1].method).toBe("HEAD");
  });

  test("the REST API answering 403 does not touch a check", async () => {
    const mod = start("0.25.0");
    serveReleases(LIVE);

    await mod.checkForUpdates();

    expect(mod.getState().status).not.toBe(mod.STATES.ERROR);
    expect(mod.getState().error).toBeNull();
    expect(feedTag()).toBe("v0.25.1");
    expect(checkSpy).toHaveBeenCalledTimes(1);
  });

  test("ten candidates fill the feed page, and a final install still finds the final", async () => {
    const candidates = Array.from({ length: 10 }, (_, i) => release(`v0.26.0-rc${10 - i}`));
    const mod = start("0.25.0");
    serveReleases([...candidates, release("v0.25.4")]);

    await mod.checkForUpdates();

    expect(fetchedUrls().filter((url) => url.includes(".atom"))).toHaveLength(1);
    expect(feedTag()).toBe("v0.25.4");
    expect(checkSpy).toHaveBeenCalledTimes(1);
  });

  test("a repository with no final release yet is not an error", async () => {
    writeConfig({ includePrereleases: true });
    const mod = start("0.25.0");
    serveReleases([release("v0.26.0-rc1")]);

    await mod.checkForUpdates();

    expect(feedTag()).toBe("v0.26.0-rc1");
  });

  test.each([
    ["the release manifest", () => `/download/v0.25.2/${manifestName()}`],
    ["the installer", () => `/download/v0.25.2/${installerName("0.25.2")}`],
    ["latest/download", () => `/latest/download/${manifestName()}`],
  ])("a 500 from %s is an error, not a skipped release", async (_label, path) => {
    const mod = start("0.25.0");
    serveReleases([release("v0.25.2"), release("v0.25.1")], {
      respond: (url) => (url.endsWith(path()) ? httpResponse(500) : undefined),
    });

    await mod.checkForUpdates();

    const state = mod.getState();
    expect(state.status).toBe(mod.STATES.ERROR);
    expect(state.error).toMatch(/HTTP 500/);
    expect(checkSpy).not.toHaveBeenCalled();
  });

  test("a rate-limited GitHub is an error that says what to do", async () => {
    const mod = start("0.25.0");
    serveReleases([], { respond: () => httpResponse(403) });

    await mod.checkForUpdates();

    const state = mod.getState();
    expect(state.status).toBe(mod.STATES.ERROR);
    expect(state.error).toMatch(/HTTP 403/);
    expect(state.error).toMatch(/hourly limit/);
    expect(checkSpy).not.toHaveBeenCalled();
  });

  test("an unreachable GitHub is an error, not 'up to date'", async () => {
    const mod = start("0.25.0");
    global.fetch = jest.fn().mockRejectedValue(new Error("getaddrinfo ENOTFOUND github.com"));

    await mod.checkForUpdates();

    const state = mod.getState();
    expect(state.status).toBe(mod.STATES.ERROR);
    expect(state.error).toMatch(/reach GitHub/);
    expect(state.error).toMatch(/ENOTFOUND/);
    expect(checkSpy).not.toHaveBeenCalled();
  });

  test("an answer that isn't a feed says it couldn't be read, not 'check your connection'", async () => {
    const mod = start("0.25.0");
    serveReleases([], { respond: () => httpResponse(200, "<!DOCTYPE html><html>Sign in</html>") });

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

  describe("a check nobody asked for", () => {
    const FIRST_CHECK_MS = 10 * 1000;
    const NEXT_CHECK_MS = 4 * 60 * 60 * 1000;

    function githubDown() {
      serveReleases([], { respond: () => httpResponse(500) });
    }

    function broadcastStatuses(win) {
      return win.webContents.send.mock.calls.map(([, state]) => state.status);
    }

    function startWatched(version) {
      jest.spyOn(electronMock.app, "getVersion").mockReturnValue(version);
      const win = makeMockWindow();
      win.webContents.send = jest.fn();
      const mod = loadModule();
      mod.init(win, INIT_OPTIONS);
      checkSpy = jest.spyOn(mockAutoUpdaterInstance, "checkForUpdates").mockResolvedValue(null);
      return { mod, win };
    }

    test("the first check failing logs and stays idle, with no error shown", async () => {
      jest.useFakeTimers();
      try {
        const { mod, win } = startWatched("0.25.0");
        githubDown();

        await jest.advanceTimersByTimeAsync(FIRST_CHECK_MS);

        expect(global.fetch).toHaveBeenCalled();
        expect(mod.getState()).toMatchObject({ status: "idle", error: null });
        expect(broadcastStatuses(win)).not.toContain("error");
      } finally {
        jest.useRealTimers();
      }
    });

    test("a scheduled check failing logs and stays idle, with no error shown", async () => {
      jest.useFakeTimers();
      try {
        const { mod, win } = startWatched("0.25.0");
        serveReleases([release("v0.25.1")]);
        await jest.advanceTimersByTimeAsync(FIRST_CHECK_MS);
        githubDown();

        await jest.advanceTimersByTimeAsync(NEXT_CHECK_MS);

        expect(fetchedUrls().filter((url) => url.endsWith(".atom"))).toHaveLength(1);
        expect(mod.getState()).toMatchObject({ status: "idle", error: null });
        expect(broadcastStatuses(win)).not.toContain("error");
      } finally {
        jest.useRealTimers();
      }
    });

    test("electron-updater failing during a scheduled check stays quiet too", async () => {
      jest.useFakeTimers();
      try {
        const { mod, win } = startWatched("0.25.0");
        serveReleases([release("v0.25.1")]);
        checkSpy.mockImplementation(async () => {
          mockAutoUpdaterInstance.emit("error", new Error("bad manifest"));
          return null;
        });

        await jest.advanceTimersByTimeAsync(FIRST_CHECK_MS);

        expect(checkSpy).toHaveBeenCalledTimes(1);
        expect(mod.getState()).toMatchObject({ status: "idle", error: null });
        expect(broadcastStatuses(win)).not.toContain("error");
      } finally {
        jest.useRealTimers();
      }
    });

    test("a manual check after a quiet failure still shows its own failure", async () => {
      jest.useFakeTimers();
      try {
        const { mod } = startWatched("0.25.0");
        githubDown();
        await jest.advanceTimersByTimeAsync(FIRST_CHECK_MS);
        expect(mod.getState().status).toBe("idle");

        await mod.checkForUpdates();

        expect(mod.getState().status).toBe("error");
        expect(mod.getState().error).toMatch(/HTTP 500/);
      } finally {
        jest.useRealTimers();
      }
    });

    test("a download the user asks for after a quiet check still shows its failure", async () => {
      jest.useFakeTimers();
      try {
        const { mod } = startWatched("0.25.0");
        serveReleases([release("v0.25.1")]);
        checkSpy.mockImplementation(async () => {
          mockAutoUpdaterInstance.emit("update-available", { version: "0.25.1" });
          return null;
        });
        await jest.advanceTimersByTimeAsync(FIRST_CHECK_MS);
        expect(mod.getState().status).toBe("available");
        jest.spyOn(mockAutoUpdaterInstance, "downloadUpdate").mockImplementationOnce(async () => {
          mockAutoUpdaterInstance.emit("error", new Error("disk full"));
          return [];
        });

        await mod.downloadUpdate();

        expect(mod.getState()).toMatchObject({ status: "error", error: "disk full" });
      } finally {
        jest.useRealTimers();
      }
    });

    test("a manual check that fails reaches the error state", async () => {
      const { mod } = startWatched("0.25.0");
      githubDown();

      const state = await electronMock.ipcMain.simulateInvoke("gaia:update:check");

      expect(state.status).toBe("error");
      expect(state.error).toMatch(/HTTP 500/);
      expect(mod.getState().status).toBe("error");
    });

    test("an automatic download failing goes idle and waits for the next check", () => {
      const { mod, win } = startWatched("0.25.0");
      mockAutoUpdaterInstance.emit("update-available", { version: "0.26.0" });
      mockAutoUpdaterInstance.emit("download-progress", { percent: 40 });

      mockAutoUpdaterInstance.emit("error", new Error("net::ERR_CONNECTION_RESET"));

      expect(mod.getState()).toMatchObject({ status: "idle", progress: 0, error: null });
      expect(broadcastStatuses(win)).not.toContain("error");
    });

    // electron-updater starts the download without awaiting it, so the failure
    // can arrive before the first progress event, with the status still "available".
    test("an automatic download failing before its first progress event goes idle too", () => {
      writeConfig({ autoUpdate: true });
      const { mod, win } = startWatched("0.25.0");
      mockAutoUpdaterInstance.emit("update-available", { version: "0.26.0" });

      mockAutoUpdaterInstance.emit("error", new Error("net::ERR_CONNECTION_RESET"));

      expect(mod.getState()).toMatchObject({ status: "idle", error: null });
      expect(broadcastStatuses(win)).not.toContain("error");
    });

    test("a routine check that fails keeps the restart the chip is offering", async () => {
      const { mod } = startWatched("0.25.0");
      mockAutoUpdaterInstance.emit("update-available", { version: "0.26.0" });
      mockAutoUpdaterInstance.emit("update-downloaded", { version: "0.26.0" });
      global.fetch = jest.fn().mockRejectedValue(new Error("offline"));

      await mod.checkForUpdates({ background: true });

      expect(mod.getState()).toMatchObject({ status: "downloaded", version: "0.26.0" });
      expect(mod.getState().lastCheckError).toMatch(/offline/);
    });

    test("a routine check can still replace a downloaded update with a newer one", async () => {
      const { mod } = startWatched("0.25.0");
      mockAutoUpdaterInstance.emit("update-available", { version: "0.26.0" });
      mockAutoUpdaterInstance.emit("update-downloaded", { version: "0.26.0" });
      serveReleases([release("v0.26.1")]);
      jest.spyOn(mockAutoUpdaterInstance, "checkForUpdates").mockImplementationOnce(async () => {
        mockAutoUpdaterInstance.emit("update-available", { version: "0.26.1" });
        return null;
      });

      await mod.checkForUpdates({ background: true });

      expect(mod.getState()).toMatchObject({ status: "available", version: "0.26.1" });
    });

    test("the same version found again keeps the restart on offer", async () => {
      const { mod } = startWatched("0.25.0");
      mockAutoUpdaterInstance.emit("update-available", { version: "0.26.0" });
      mockAutoUpdaterInstance.emit("update-downloaded", { version: "0.26.0" });
      serveReleases([release("v0.26.0")]);
      jest.spyOn(mockAutoUpdaterInstance, "checkForUpdates").mockImplementationOnce(async () => {
        mockAutoUpdaterInstance.emit("update-available", { version: "0.26.0" });
        return null;
      });

      await mod.checkForUpdates({ background: true });

      expect(mod.getState()).toMatchObject({ status: "downloaded", version: "0.26.0" });
    });

    test("a download started by a check the user asked for reports its failure", async () => {
      writeConfig({ autoUpdate: true });
      const { mod } = startWatched("0.25.0");
      serveReleases([release("v0.26.0")]);
      await mod.checkForUpdates();
      mockAutoUpdaterInstance.emit("update-available", { version: "0.26.0" });

      mockAutoUpdaterInstance.emit("error", new Error("disk full"));

      expect(mod.getState()).toMatchObject({ status: "error", error: "disk full" });
    });

    test("an unasked failure stays out of the chip but is kept for Settings", () => {
      writeConfig({ autoUpdate: true });
      const { mod } = startWatched("0.25.0");
      mockAutoUpdaterInstance.emit("update-available", { version: "0.26.0" });

      mockAutoUpdaterInstance.emit("error", new Error("net::ERR_CONNECTION_RESET"));

      expect(mod.getState().lastCheckError).toMatch(/ERR_CONNECTION_RESET/);
    });

    test("a download the user asked for that fails reaches the error state", async () => {
      const { mod } = startWatched("0.25.0");
      mockAutoUpdaterInstance.emit("update-available", { version: "0.26.0" });
      jest.spyOn(mockAutoUpdaterInstance, "downloadUpdate").mockImplementationOnce(async () => {
        mockAutoUpdaterInstance.emit("error", new Error("disk full"));
        return [];
      });

      await mod.downloadUpdate();

      expect(mod.getState()).toMatchObject({ status: "error", error: "disk full" });
    });
  });
});
