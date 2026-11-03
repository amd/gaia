// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * A finished download only updates the chip; the restart happens when the user
 * clicks it, or by itself for a version they picked. Either way it asks before
 * it cuts off running work (src/gaia/apps/webui/services/auto-updater.cjs,
 * init's confirmRestart). quitAndInstall starts the installer at once, so the
 * question has to come first; the quit that follows skips the quit guard.
 */

"use strict";

const os = require("os");
const path = require("path");
const fs = require("fs");

const electronMock = require("electron");
const { autoUpdater: mockUpdater } = require("electron-updater");
const { refuseFetch } = require("./_helpers/github-releases.cjs");

let tmpHome;
let loaded = [];

beforeEach(() => {
  tmpHome = fs.mkdtempSync(path.join(os.tmpdir(), "gaia-test-"));
  jest.spyOn(os, "homedir").mockReturnValue(tmpHome);
  jest.clearAllMocks();
  mockUpdater.removeAllListeners();
  mockUpdater.quitAndInstall = jest.fn();
  mockUpdater.checkForUpdates = jest.fn(async () => null);
  mockUpdater.downloadUpdate = jest.fn(async () => []);
  refuseFetch();
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

async function finishDownload() {
  mockUpdater.emit("update-downloaded", { version: "0.26.0" });
  for (let i = 0; i < 5; i++) await flush();
}

const clickRestartChip = () => electronMock.ipcMain.simulateInvoke("gaia:update:restart");

function saveConfig(cfg) {
  fs.mkdirSync(path.join(tmpHome, ".gaia"), { recursive: true });
  fs.writeFileSync(path.join(tmpHome, ".gaia", "update-config.json"), JSON.stringify(cfg));
}

test("a finished download changes the chip and nothing else", async () => {
  const confirmRestart = jest.fn(async () => true);
  const m = initUpdater({ confirmRestart });
  await finishDownload();
  expect(m.getState()).toMatchObject({ status: "downloaded", version: "0.26.0", progress: 100 });
  expect(electronMock.dialog.showMessageBox).not.toHaveBeenCalled();
  expect(electronMock.dialog.showErrorBox).not.toHaveBeenCalled();
  expect(confirmRestart).not.toHaveBeenCalled();
  expect(mockUpdater.quitAndInstall).not.toHaveBeenCalled();
});

test("the chip restarts silently and reopens the app when nothing would be lost", async () => {
  const confirmRestart = jest.fn(async () => true);
  initUpdater({ confirmRestart });
  await finishDownload();
  await clickRestartChip();
  expect(confirmRestart).toHaveBeenCalledTimes(1);
  expect(mockUpdater.quitAndInstall).toHaveBeenCalledTimes(1);
  expect(mockUpdater.quitAndInstall).toHaveBeenCalledWith(true, true);
});

test("Keep running puts the restart off; the update installs on the next quit", async () => {
  saveConfig({ autoUpdate: true });
  const confirmRestart = jest.fn(async () => false);
  const m = initUpdater({ confirmRestart });
  await finishDownload();
  await clickRestartChip();
  expect(confirmRestart).toHaveBeenCalledTimes(1);
  expect(mockUpdater.quitAndInstall).not.toHaveBeenCalled();
  expect(mockUpdater.autoInstallOnAppQuit).toBe(true);
  expect(m.getState().status).toBe("downloaded");
});

test("a failing restart check reaches the chip's caller, and nothing installs", async () => {
  const confirmRestart = jest.fn(async () => {
    throw new Error("dialog failed");
  });
  initUpdater({ confirmRestart });
  await finishDownload();
  await expect(clickRestartChip()).rejects.toThrow(/dialog failed/);
  expect(mockUpdater.quitAndInstall).not.toHaveBeenCalled();
  expect(electronMock.dialog.showErrorBox).not.toHaveBeenCalled();
});

describe("a version picked in Settings", () => {
  function pickedInstallWorks() {
    mockUpdater.checkForUpdates = jest.fn(async () => {
      mockUpdater.emit("update-available", { version: "0.24.0" });
      return null;
    });
    mockUpdater.downloadUpdate = jest.fn(async () => {
      mockUpdater.emit("update-downloaded", { version: "0.24.0" });
      return [];
    });
  }

  // The restart reads free disk space first, which takes real I/O.
  const settle = () => new Promise((r) => setTimeout(r, 50));

  test("restarts by itself once downloaded, asking about running work first", async () => {
    const confirmRestart = jest.fn(async () => true);
    const m = initUpdater({ confirmRestart });
    pickedInstallWorks();

    await m.installVersion("v0.24.0");
    await settle();

    expect(confirmRestart).toHaveBeenCalledTimes(1);
    expect(mockUpdater.quitAndInstall).toHaveBeenCalledTimes(1);
    expect(mockUpdater.quitAndInstall).toHaveBeenCalledWith(true, true);
    expect(electronMock.dialog.showMessageBox).not.toHaveBeenCalled();
  });

  test("stays on the chip when the user keeps working, and the chip still restarts", async () => {
    const confirmRestart = jest.fn(async () => false);
    const m = initUpdater({ confirmRestart });
    pickedInstallWorks();

    await m.installVersion("v0.24.0");
    await settle();

    expect(mockUpdater.quitAndInstall).not.toHaveBeenCalled();
    expect(m.getState()).toMatchObject({ status: "downloaded", version: "0.24.0", error: null });

    confirmRestart.mockResolvedValue(true);
    await clickRestartChip();
    expect(mockUpdater.quitAndInstall).toHaveBeenCalledWith(true, true);
  });

  test("restarts only once: a later download waits for the chip", async () => {
    const confirmRestart = jest.fn(async () => false);
    const m = initUpdater({ confirmRestart });
    pickedInstallWorks();
    await m.installVersion("v0.24.0");
    await settle();
    expect(confirmRestart).toHaveBeenCalledTimes(1);

    await finishDownload();

    expect(confirmRestart).toHaveBeenCalledTimes(1);
  });

  test("a failing restart check is shown, and nothing installs", async () => {
    const m = initUpdater({
      confirmRestart: jest.fn(async () => {
        throw new Error("dialog failed");
      }),
    });
    pickedInstallWorks();

    await expect(m.installVersion("v0.24.0")).rejects.toThrow("dialog failed");

    expect(m.getState().status).toBe("downloaded");
    expect(mockUpdater.quitAndInstall).not.toHaveBeenCalled();
  });

  test.each([
    ["its download fails", () => {
      pickedInstallWorks();
      mockUpdater.downloadUpdate = jest.fn(async () => {
        throw new Error("net down");
      });
    }, /net down/],
    ["it has no installer here", () => {}, /no installer/],
  ])("when %s, a later unrelated update does not restart the app", async (_label, arrange, failure) => {
    const confirmRestart = jest.fn(async () => true);
    const m = initUpdater({ confirmRestart });
    arrange();
    await expect(m.installVersion("v0.24.0")).rejects.toThrow(failure);

    await finishDownload();

    expect(m.getState().status).toBe("downloaded");
    expect(confirmRestart).not.toHaveBeenCalled();
    expect(mockUpdater.quitAndInstall).not.toHaveBeenCalled();
  });
});

test.each([[undefined], [{}], [{ confirmRestart: true }]])(
  "init without a restart check (%p) fails loudly instead of restarting silently",
  (options) => {
    expect(() => initUpdater(options)).toThrow(/needs options.confirmRestart/);
  }
);

test("main.cjs hands init the restart check and the backend prefetch", () => {
  // main.cjs can't be required without launching Electron; init() throwing
  // above is what makes a dropped option fail at startup, this pins main's call.
  const main = fs.readFileSync(
    path.join(__dirname, "..", "..", "src", "gaia", "apps", "webui", "main.cjs"),
    "utf8"
  );
  expect(main).toContain(
    "autoUpdater.init(mainWindow, { confirmRestart: confirmUpdateRestart, ...updateBackendHooks() })"
  );
  expect(main).toContain("autoUpdater.init(null, { confirmRestart: async () => true, ...updateBackendHooks() })");
  expect(main).toMatch(/prefetchBackend: \(version\) => backendInstaller\.prefetchBackend\(version/);
});

test("turning auto-update off stops an unasked download from installing on quit", async () => {
  saveConfig({ autoUpdate: true });
  const m = initUpdater({ confirmRestart: jest.fn(async () => false) });
  await finishDownload();
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
  expect(mockUpdater.getFeedURL()).toEqual({
    provider: "generic",
    url: "https://github.com/amd/gaia/releases/download/v0.24.0/",
    useMultipleRangeRequest: false,
  });
});

test("auto-update switched off in the Terminal UI after a download stops the install on quit", async () => {
  saveConfig({ autoUpdate: true });
  initUpdater({ confirmRestart: jest.fn(async () => false) });
  await finishDownload();
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

describe("a crashed app's lock that cannot be moved aside", () => {
  function staleLock() {
    const dir = path.join(tmpHome, ".gaia");
    fs.mkdirSync(dir, { recursive: true });
    const lock = path.join(dir, "update-config.json.lock");
    fs.writeFileSync(lock, "");
    const hourAgo = new Date(Date.now() - 3600 * 1000);
    fs.utimesSync(lock, hourAgo, hourAgo);
    return lock;
  }

  // Throws `code` for the lock's rename; after 2,000 tries a non-retryable
  // error breaks a loop that would otherwise spin forever.
  function failRename(code, onCall) {
    const realRename = fs.renameSync;
    let calls = 0;
    const spy = jest.spyOn(fs, "renameSync").mockImplementation((from, to) => {
      if (!String(from).endsWith(".lock")) return realRename(from, to);
      if (++calls > 2000) {
        const loop = new Error("rename retried without end");
        loop.code = "ELOOPED";
        throw loop;
      }
      if (onCall) onCall(from);
      const err = new Error(`${code}: operation not permitted, rename '${from}'`);
      err.code = code;
      throw err;
    });
    return { spy, calls: () => calls };
  }

  for (const code of ["EACCES", "EPERM"]) {
    test(`gives up within the wait and names permissions (${code})`, async () => {
      const m = initUpdater({ confirmRestart: jest.fn(async () => true) });
      const lock = staleLock();
      const { spy } = failRename(code);
      try {
        const start = Date.now();
        const err = await m.setAutoUpdate(true).catch((e) => e);
        expect(err.message).toMatch(/Cannot remove .*update-config\.json\.lock/);
        expect(err.message).toMatch(/permissions/);
        expect(err.message).not.toMatch(/busy|retried without end/);
        expect(Date.now() - start).toBeLessThan(2000);
        expect(fs.existsSync(lock)).toBe(true);
      } finally {
        spy.mockRestore();
      }
    });
  }

  test("a lock another app already moved aside is not an error", async () => {
    const m = initUpdater({ confirmRestart: jest.fn(async () => true) });
    staleLock();
    const { spy } = failRename("ENOENT", (from) => fs.rmSync(from, { force: true }));
    try {
      await m.setAutoUpdate(false);
    } finally {
      spy.mockRestore();
    }
    const saved = JSON.parse(
      fs.readFileSync(path.join(tmpHome, ".gaia", "update-config.json"), "utf8")
    );
    expect(saved.autoUpdate).toBe(false);
  });

  test("an unexpected rename error surfaces at once", async () => {
    const m = initUpdater({ confirmRestart: jest.fn(async () => true) });
    staleLock();
    const { spy, calls } = failRename("EIO");
    try {
      const err = await m.setAutoUpdate(true).catch((e) => e);
      expect(err.message).toMatch(/EIO/);
      expect(calls()).toBe(1);
    } finally {
      spy.mockRestore();
    }
  });
});

describe("a silent install that fails", () => {
  const attemptFile = () => path.join(tmpHome, ".gaia", "update-attempt.json");

  function failedLastTime(attempt = { from: "0.25.0", target: "0.26.0" }) {
    fs.mkdirSync(path.dirname(attemptFile()), { recursive: true });
    fs.writeFileSync(attemptFile(), JSON.stringify(attempt));
  }

  test("a restart notes what it is installing before it quits", async () => {
    const m = initUpdater({ confirmRestart: async () => true });
    await finishDownload();
    await clickRestartChip();
    expect(JSON.parse(fs.readFileSync(attemptFile(), "utf8"))).toEqual({
      from: "0.25.0",
      target: "0.26.0",
    });
    expect(m.getState().installFailed).toBeNull();
  });

  test("the next launch says so and the next try shows the installer's window", async () => {
    failedLastTime();
    const m = initUpdater({ confirmRestart: async () => true });
    expect(m.getState().installFailed).toEqual({ target: "0.26.0", from: "0.25.0" });
    await finishDownload();
    await clickRestartChip();
    expect(mockUpdater.quitAndInstall).toHaveBeenCalledWith(false, true);
  });

  test("a failed install is reported once, and the launch after installs silently again", async () => {
    failedLastTime();
    initUpdater({ confirmRestart: async () => true });
    expect(fs.existsSync(attemptFile())).toBe(false);
    loaded.pop().destroy();
    mockUpdater.removeAllListeners();
    const m = initUpdater({ confirmRestart: async () => true });
    expect(m.getState().installFailed).toBeNull();
    await finishDownload();
    await clickRestartChip();
    expect(mockUpdater.quitAndInstall).toHaveBeenCalledWith(true, true);
  });

  test("after the failure is reported, the next launch installs on quit again", async () => {
    saveConfig({ autoUpdate: true });
    failedLastTime();
    let m = initUpdater({ confirmRestart: async () => true });
    expect(mockUpdater.autoInstallOnAppQuit).toBe(false);
    m.destroy();
    mockUpdater.removeAllListeners();
    const on = jest.spyOn(electronMock.app, "on");
    m = initUpdater({ confirmRestart: async () => true });
    expect(m.getState().installFailed).toBeNull();
    expect(mockUpdater.autoInstallOnAppQuit).toBe(true);
    await finishDownload();
    on.mock.calls.filter(([event]) => event === "before-quit").at(-1)[1]();
    expect(JSON.parse(fs.readFileSync(attemptFile(), "utf8"))).toEqual({
      from: "0.25.0",
      target: "0.26.0",
    });
  });

  test("a retry that fails again is reported again, then forgotten", async () => {
    failedLastTime();
    let m = initUpdater({ confirmRestart: async () => true });
    await finishDownload();
    await clickRestartChip();
    expect(mockUpdater.quitAndInstall).toHaveBeenCalledWith(false, true);
    m.destroy();
    mockUpdater.removeAllListeners();
    mockUpdater.quitAndInstall.mockClear();

    m = initUpdater({ confirmRestart: async () => true });
    expect(m.getState().installFailed).toEqual({ target: "0.26.0", from: "0.25.0" });
    expect(fs.existsSync(attemptFile())).toBe(false);
    m.destroy();
    mockUpdater.removeAllListeners();

    m = initUpdater({ confirmRestart: async () => true });
    expect(m.getState().installFailed).toBeNull();
    await finishDownload();
    await clickRestartChip();
    expect(mockUpdater.quitAndInstall).toHaveBeenCalledWith(true, true);
  });

  test("an attempt record that can't be deleted still reports the failure", () => {
    failedLastTime();
    const realRm = fs.rmSync;
    const rm = jest.spyOn(fs, "rmSync").mockImplementation((target, opts) => {
      if (target === attemptFile()) throw Object.assign(new Error("EPERM"), { code: "EPERM" });
      return realRm(target, opts);
    });
    const m = initUpdater({ confirmRestart: async () => true });
    rm.mockRestore();
    expect(m.getState().installFailed).toEqual({ target: "0.26.0", from: "0.25.0" });
    expect(fs.existsSync(attemptFile())).toBe(true);
  });

  test("a failed install doesn't make a later backend failure blame an old update", () => {
    saveConfig({ previousAppVersion: "0.24.0", launchesSinceUpdate: 4 });
    failedLastTime();
    let m;
    jest.isolateModules(() => {
      m = require("../../src/gaia/apps/webui/services/auto-updater.cjs");
    });
    m.reportStartFailure();
    expect(m.getState().startFailed).toBe(false);
    m = initUpdater({ confirmRestart: async () => true });
    m.reportStartFailure();
    expect(m.getState().startFailed).toBe(false);
    m.destroy();
    m = initUpdater({ confirmRestart: async () => true });
    m.reportStartFailure();
    expect(m.getState().startFailed).toBe(false);
  });

  test("a launch on a different version counts as installed and forgets the attempt", async () => {
    failedLastTime({ from: "0.24.0", target: "0.25.0" });
    const m = initUpdater({ confirmRestart: async () => true });
    expect(m.getState().installFailed).toBeNull();
    expect(fs.existsSync(attemptFile())).toBe(false);
    await finishDownload();
    await clickRestartChip();
    expect(mockUpdater.quitAndInstall).toHaveBeenCalledWith(true, true);
  });

  test("an install on quit is noted too", async () => {
    saveConfig({ autoUpdate: true });
    const on = jest.spyOn(electronMock.app, "on");
    initUpdater({ confirmRestart: async () => true });
    await finishDownload();
    const beforeQuit = on.mock.calls.find(([event]) => event === "before-quit")[1];
    beforeQuit();
    expect(fs.existsSync(attemptFile())).toBe(true);
  });

  test("a quit with nothing to install notes nothing", () => {
    const on = jest.spyOn(electronMock.app, "on");
    initUpdater({ confirmRestart: async () => true });
    on.mock.calls.find(([event]) => event === "before-quit")[1]();
    expect(fs.existsSync(attemptFile())).toBe(false);
  });

  test("a nearly full disk refuses the restart while the app still works", async () => {
    const statfs = jest
      .spyOn(fs.promises, "statfs")
      .mockResolvedValue({ bavail: 100, bsize: 1024 * 1024 });
    initUpdater({ confirmRestart: async () => true });
    await finishDownload();
    await expect(clickRestartChip()).rejects.toThrow(/needs about 1 GB free.*100 MB/);
    expect(mockUpdater.quitAndInstall).not.toHaveBeenCalled();
    statfs.mockRestore();
  });
});

describe("a download nobody wants any more", () => {
  test("turning release candidates off drops a downloaded candidate", async () => {
    saveConfig({ autoUpdate: true, includePrereleases: true });
    const m = initUpdater({ confirmRestart: async () => true });
    mockUpdater.emit("update-downloaded", { version: "0.26.0-rc.1" });
    await flush();
    await m.setIncludePrereleases(false);
    expect(m.getState()).toMatchObject({ status: "idle", version: null });
    expect(mockUpdater.autoInstallOnAppQuit).toBe(false);
  });

  test("turning release candidates off keeps a downloaded final", async () => {
    saveConfig({ autoUpdate: true, includePrereleases: true });
    const m = initUpdater({ confirmRestart: async () => true });
    await finishDownload();
    await m.setIncludePrereleases(false);
    expect(m.getState()).toMatchObject({ status: "downloaded", version: "0.26.0" });
    expect(mockUpdater.autoInstallOnAppQuit).toBe(true);
  });

  test("unpinning drops the pinned download so a quit can't install it", async () => {
    saveConfig({ autoUpdate: true });
    const m = initUpdater({ confirmRestart: async () => true });
    await finishDownload();
    m.clearPin();
    expect(m.getState().status).toBe("idle");
    expect(mockUpdater.autoInstallOnAppQuit).toBe(false);
  });

  test("a later download can install on quit again", async () => {
    saveConfig({ autoUpdate: true });
    const m = initUpdater({ confirmRestart: async () => true });
    await finishDownload();
    m.clearPin();
    await finishDownload();
    expect(mockUpdater.autoInstallOnAppQuit).toBe(true);
  });
});

describe("going back after a bad update", () => {
  const attemptFile = () => path.join(tmpHome, ".gaia", "update-attempt.json");
  const configFile = () => path.join(tmpHome, ".gaia", "update-config.json");

  function updatedFrom(version) {
    electronMock.app.getVersion = () => "0.26.0";
    fs.mkdirSync(path.dirname(attemptFile()), { recursive: true });
    fs.writeFileSync(attemptFile(), JSON.stringify({ from: version, target: "0.26.0" }));
  }

  function feedServes(version) {
    jest.spyOn(mockUpdater, "setFeedURL");
    mockUpdater.checkForUpdates = jest.fn(async () => {
      mockUpdater.emit("update-available", { version });
      return null;
    });
    mockUpdater.downloadUpdate = jest.fn(async () => {
      mockUpdater.emit("update-downloaded", { version });
      return [];
    });
  }

  test("a landed update remembers where it came from, across launches", () => {
    updatedFrom("0.25.0");
    let m = initUpdater({ confirmRestart: async () => true });
    expect(m.getState().previousVersion).toBe("0.25.0");
    expect(fs.existsSync(attemptFile())).toBe(false);
    m.destroy();
    m = initUpdater({ confirmRestart: async () => true });
    expect(m.getState().previousVersion).toBe("0.25.0");
  });

  test("a failed install has nothing to go back to", () => {
    fs.mkdirSync(path.dirname(attemptFile()), { recursive: true });
    fs.writeFileSync(attemptFile(), JSON.stringify({ from: "0.25.0", target: "0.26.0" }));
    const m = initUpdater({ confirmRestart: async () => true });
    expect(m.getState().previousVersion).toBeNull();
  });

  test("the way back is known before the updater starts, for a failed first launch", () => {
    updatedFrom("0.25.0");
    let m;
    jest.isolateModules(() => {
      m = require("../../src/gaia/apps/webui/services/auto-updater.cjs");
    });
    expect(m.previousVersion()).toBe("0.25.0");
  });

  test("going back installs the old version, pins it and restarts", async () => {
    updatedFrom("0.25.0");
    const m = initUpdater({ confirmRestart: async () => true });
    feedServes("0.25.0");
    await m.revertUpdate();
    expect(mockUpdater.allowDowngrade).toBe(true);
    expect(mockUpdater.setFeedURL.mock.calls.at(-1)[0].url).toMatch(/\/v0\.25\.0\/?$/);
    expect(JSON.parse(fs.readFileSync(configFile(), "utf8")).pinnedVersion).toBe("v0.25.0");
    expect(mockUpdater.quitAndInstall).toHaveBeenCalledTimes(1);
    expect(m.getState().previousVersion).toBeNull();
  });

  test("a release candidate goes back to its own tag", async () => {
    updatedFrom("0.25.4-rc.2");
    const m = initUpdater({ confirmRestart: async () => true });
    feedServes("0.25.4-rc.2");
    await m.revertUpdate();
    expect(mockUpdater.setFeedURL.mock.calls.at(-1)[0].url).toMatch(/\/v0\.25\.4-rc2\/?$/);
  });

  test("a refused restart throws, drops the pin and download, and keeps the way back", async () => {
    updatedFrom("0.25.0");
    const statfs = jest
      .spyOn(fs.promises, "statfs")
      .mockResolvedValue({ bavail: 100, bsize: 1024 * 1024 });
    const m = initUpdater({ confirmRestart: async () => true });
    feedServes("0.25.0");
    m.reportStartFailure();
    await expect(m.revertUpdate()).rejects.toThrow(/needs about 1 GB free/);
    expect(mockUpdater.quitAndInstall).not.toHaveBeenCalled();
    expect(JSON.parse(fs.readFileSync(configFile(), "utf8")).pinnedVersion).toBeFalsy();
    expect(m.getState()).toMatchObject({ status: "idle", previousVersion: "0.25.0", startFailed: true });
    expect(mockUpdater.autoInstallOnAppQuit).toBe(false);
    statfs.mockRestore();
  });

  test("a failed download keeps the way back and drops the pin", async () => {
    updatedFrom("0.25.0");
    const m = initUpdater({ confirmRestart: async () => true });
    feedServes("0.25.0");
    mockUpdater.downloadUpdate = jest.fn(async () => {
      throw new Error("net::ERR_INTERNET_DISCONNECTED");
    });
    await expect(m.revertUpdate()).rejects.toThrow(/ERR_INTERNET_DISCONNECTED/);
    expect(JSON.parse(fs.readFileSync(configFile(), "utf8")).pinnedVersion).toBeFalsy();
    expect(m.previousVersion()).toBe("0.25.0");
    expect(m.getState().previousVersion).toBe("0.25.0");
  });

  test("a failed go-back leaves the chip's offer in place", async () => {
    updatedFrom("0.25.0");
    const m = initUpdater({ confirmRestart: async () => true });
    m.reportStartFailure();
    feedServes("0.25.0");
    mockUpdater.downloadUpdate = jest.fn(async () => {
      throw new Error("offline");
    });
    await expect(m.revertUpdate()).rejects.toThrow(/offline/);
    expect(m.getState().startFailed).toBe(true);
  });

  test("an install that didn't land doesn't stay pinned", () => {
    saveConfig({ pinnedVersion: "v0.25.0", previousAppVersion: "0.25.0" });
    electronMock.app.getVersion = () => "0.26.0";
    fs.mkdirSync(path.dirname(attemptFile()), { recursive: true });
    fs.writeFileSync(attemptFile(), JSON.stringify({ from: "0.26.0", target: "0.25.0" }));
    const m = initUpdater({ confirmRestart: async () => true });
    expect(m.getState()).toMatchObject({
      pinnedVersion: null,
      previousVersion: "0.25.0",
      installFailed: { target: "0.25.0", from: "0.26.0" },
    });
  });

  test("a go-back that didn't land is reported once; the launch after updates normally", () => {
    saveConfig({ autoUpdate: true, pinnedVersion: "v0.25.0", previousAppVersion: "0.25.0" });
    electronMock.app.getVersion = () => "0.26.0";
    fs.mkdirSync(path.dirname(attemptFile()), { recursive: true });
    fs.writeFileSync(attemptFile(), JSON.stringify({ from: "0.26.0", target: "0.25.0" }));
    let m = initUpdater({ confirmRestart: async () => true });
    expect(m.getState().installFailed).toEqual({ target: "0.25.0", from: "0.26.0" });
    expect(fs.existsSync(attemptFile())).toBe(false);
    m.destroy();
    m = initUpdater({ confirmRestart: async () => true });
    expect(m.getState()).toMatchObject({ installFailed: null, pinnedVersion: null, previousVersion: "0.25.0" });
    expect(mockUpdater.autoInstallOnAppQuit).toBe(true);
  });

  test("a landed update installs the next one on quit", () => {
    saveConfig({ autoUpdate: true });
    updatedFrom("0.25.0");
    const m = initUpdater({ confirmRestart: async () => true });
    expect(m.getState().installFailed).toBeNull();
    expect(mockUpdater.autoInstallOnAppQuit).toBe(true);
  });

  test("a pinned launch checks the pinned release, not a dead default feed", () => {
    saveConfig({ pinnedVersion: "v0.25.0" });
    jest.spyOn(mockUpdater, "setFeedURL");
    initUpdater({ confirmRestart: async () => true });
    expect(mockUpdater.setFeedURL.mock.calls.at(-1)[0].url).toMatch(/\/v0\.25\.0\/?$/);
  });

  test("a slow backend that came up clears the offer", () => {
    updatedFrom("0.25.0");
    const m = initUpdater({ confirmRestart: async () => true });
    m.reportStartFailure();
    m.clearStartFailure();
    expect(m.getState().startFailed).toBe(false);
  });

  test("a backend failure before the updater starts still raises the offer", () => {
    updatedFrom("0.25.0");
    let m;
    jest.isolateModules(() => {
      m = require("../../src/gaia/apps/webui/services/auto-updater.cjs");
    });
    m.reportStartFailure();
    expect(m.getState()).toMatchObject({ startFailed: true, previousVersion: "0.25.0" });
  });

  test("the version gone back to is not offered as the way back", () => {
    saveConfig({ previousAppVersion: "0.26.0" });
    const m = initUpdater({ confirmRestart: async () => true });
    expect(m.previousVersion()).toBeNull();
  });

  test("a pin means the version was chosen, so there is nothing to undo", () => {
    saveConfig({ previousAppVersion: "0.24.0", pinnedVersion: "v0.25.0" });
    const m = initUpdater({ confirmRestart: async () => true });
    expect(m.previousVersion()).toBeNull();
  });

  test("with nothing to go back to the error says what to do", async () => {
    const m = initUpdater({ confirmRestart: async () => true });
    await expect(m.revertUpdate()).rejects.toThrow(/no earlier GAIA version.*Settings/);
  });

  test("the backend failing after an update raises the offer", () => {
    updatedFrom("0.25.0");
    const m = initUpdater({ confirmRestart: async () => true });
    expect(m.getState().startFailed).toBe(false);
    m.reportStartFailure();
    expect(m.getState().startFailed).toBe(true);
  });

  test("a backend failure only blames the update for its first three launches", () => {
    updatedFrom("0.25.0");
    for (let launch = 1; launch <= 3; launch += 1) {
      const m = initUpdater({ confirmRestart: async () => true });
      m.reportStartFailure();
      expect(m.getState().startFailed).toBe(true);
      m.destroy();
    }
    const m = initUpdater({ confirmRestart: async () => true });
    m.reportStartFailure();
    expect(m.getState()).toMatchObject({ startFailed: false, previousVersion: "0.25.0" });
  });

  test("a backend failure with no earlier version offers nothing", () => {
    const m = initUpdater({ confirmRestart: async () => true });
    m.reportStartFailure();
    expect(m.getState().startFailed).toBe(false);
  });
});

describe("an install that can't finish", () => {
  beforeEach(() => {
    electronMock.net = { isOnline: () => false };
  });
  afterEach(() => {
    delete electronMock.net;
  });

  test("offline, a restart is refused and the update stays on offer", async () => {
    const m = initUpdater({ confirmRestart: async () => true });
    await finishDownload();
    await expect(clickRestartChip()).rejects.toThrow(/needs the internet to finish this update/);
    expect(mockUpdater.quitAndInstall).not.toHaveBeenCalled();
    expect(m.getState().status).toBe("downloaded");
  });

  test("offline, a restart goes ahead once the update's backend is cached", async () => {
    const fetched = new Set();
    const prefetchBackend = jest.fn(async (version) => {
      fetched.add(version);
    });
    const m = initUpdater({
      confirmRestart: async () => true,
      prefetchBackend,
      isBackendPrefetched: (version) => fetched.has(version),
    });
    await finishDownload();
    expect(prefetchBackend).toHaveBeenCalledWith("0.26.0");
    await clickRestartChip();
    expect(mockUpdater.quitAndInstall).toHaveBeenCalledTimes(1);
    expect(m.getState().status).toBe("downloaded");
  });

  test("the chip offers the restart only after the backend fetch ends", async () => {
    let finish;
    const m = initUpdater({
      confirmRestart: async () => true,
      prefetchBackend: () => new Promise((resolve) => (finish = resolve)),
      isBackendPrefetched: () => false,
    });
    await finishDownload();
    expect(m.getState().status).not.toBe("downloaded");
    finish();
    await flush();
    expect(m.getState().status).toBe("downloaded");
  });

  function pickedVersionServes(version) {
    jest.spyOn(mockUpdater, "setFeedURL");
    mockUpdater.checkForUpdates = jest.fn(async () => {
      mockUpdater.emit("update-available", { version });
      return null;
    });
    mockUpdater.downloadUpdate = jest.fn(async () => {
      mockUpdater.emit("update-downloaded", { version });
      return [];
    });
  }

  test("a picked version waits for its backend fetch, then restarts", async () => {
    electronMock.net = { isOnline: () => true };
    let finish;
    const m = initUpdater({
      confirmRestart: async () => true,
      prefetchBackend: () => new Promise((resolve) => (finish = resolve)),
      isBackendPrefetched: () => false,
    });
    pickedVersionServes("0.24.0");
    let settled = false;
    const picked = m.installVersion("v0.24.0").then(() => (settled = true));
    await flush();
    expect(settled).toBe(false);
    expect(mockUpdater.quitAndInstall).not.toHaveBeenCalled();
    finish();
    await picked;
    expect(mockUpdater.quitAndInstall).toHaveBeenCalledTimes(1);
  });

  test("a picked version's refusal after its backend fetch reaches the caller", async () => {
    const m = initUpdater({
      confirmRestart: async () => true,
      prefetchBackend: async () => {
        throw new Error("PyPI is down");
      },
      isBackendPrefetched: () => false,
    });
    pickedVersionServes("0.24.0");
    await expect(m.installVersion("v0.24.0")).rejects.toThrow(/needs the internet/);
    expect(mockUpdater.quitAndInstall).not.toHaveBeenCalled();
  });

  test("a quit while the backend is fetched installs nothing", async () => {
    saveConfig({ autoUpdate: true });
    let finish;
    const m = initUpdater({
      confirmRestart: async () => true,
      prefetchBackend: () => new Promise((resolve) => (finish = resolve)),
      isBackendPrefetched: () => false,
    });
    mockUpdater.emit("update-downloaded", { version: "0.26.0" });
    await flush();
    expect(m.getState()).toMatchObject({ status: "downloading", preparing: true });
    expect(mockUpdater.autoInstallOnAppQuit).toBe(false);
    finish();
    await flush();
    expect(m.getState()).toMatchObject({ status: "downloaded", preparing: false });
    expect(mockUpdater.autoInstallOnAppQuit).toBe(true);
  });

  test("an update given up on during the backend fetch is not offered after it", async () => {
    saveConfig({ includePrereleases: true });
    let finish;
    const m = initUpdater({
      confirmRestart: async () => true,
      prefetchBackend: () => new Promise((resolve) => (finish = resolve)),
      isBackendPrefetched: () => false,
    });
    mockUpdater.emit("update-downloaded", { version: "0.26.0-rc.1" });
    await flush();
    mockUpdater.emit("update-not-available", { version: "0.25.0" });
    finish();
    await flush();
    expect(m.getState().status).toBe("idle");
  });

  test("a second download of the same version waits on the first fetch", async () => {
    const prefetchBackend = jest.fn(() => new Promise((resolve) => setTimeout(resolve, 5)));
    initUpdater({ confirmRestart: async () => true, prefetchBackend, isBackendPrefetched: () => false });
    mockUpdater.emit("update-downloaded", { version: "0.26.0" });
    mockUpdater.emit("update-downloaded", { version: "0.26.0" });
    await new Promise((r) => setTimeout(r, 20));
    expect(prefetchBackend).toHaveBeenCalledTimes(1);
  });

  test("a download error with electron-updater's own code still reads once, then the pin note", async () => {
    electronMock.net = { isOnline: () => true };
    const m = initUpdater({ confirmRestart: async () => true });
    pickedVersionServes("0.24.0");
    mockUpdater.downloadUpdate = jest.fn(async () => {
      throw Object.assign(new Error("HttpError: 404"), { code: "HTTP_ERROR_404" });
    });
    const err = await m.installVersion("v0.24.0").catch((e) => e);
    expect(err).toMatchObject({
      code: "failedWith",
      params: { reason: "HttpError: 404" },
      note: { code: "notPaused" },
    });
  });

  test("a backend already fetched is not fetched again", async () => {
    const prefetchBackend = jest.fn(async () => {});
    initUpdater({ confirmRestart: async () => true, prefetchBackend, isBackendPrefetched: () => true });
    await finishDownload();
    expect(prefetchBackend).not.toHaveBeenCalled();
  });

  test("a failed backend fetch still offers the update, which then needs the internet", async () => {
    const m = initUpdater({
      confirmRestart: async () => true,
      prefetchBackend: async () => {
        throw new Error("PyPI is down");
      },
      isBackendPrefetched: () => false,
    });
    await finishDownload();
    expect(m.getState().status).toBe("downloaded");
    await expect(clickRestartChip()).rejects.toThrow(/needs the internet/);
  });

  test("a picked version's refused restart says so and stays on offer", async () => {
    const m = initUpdater({ confirmRestart: async () => true });
    jest.spyOn(mockUpdater, "setFeedURL");
    mockUpdater.checkForUpdates = jest.fn(async () => {
      mockUpdater.emit("update-available", { version: "0.24.0" });
      return null;
    });
    mockUpdater.downloadUpdate = jest.fn(async () => {
      mockUpdater.emit("update-downloaded", { version: "0.24.0" });
      return [];
    });
    await expect(m.installVersion("v0.24.0")).rejects.toThrow(/needs the internet/);
    expect(m.getState()).toMatchObject({ status: "downloaded", pinnedVersion: "v0.24.0" });
  });

  function quitWith(event) {
    saveConfig({ autoUpdate: true });
    const on = jest.spyOn(electronMock.app, "on");
    initUpdater({ confirmRestart: async () => true });
    return finishDownload().then(() => on.mock.calls.find(([e]) => e === "before-quit")[1](event));
  }
  const attemptFile = () => path.join(tmpHome, ".gaia", "update-attempt.json");

  test("offline, a quit leaves the update for later", async () => {
    await quitWith();
    expect(mockUpdater.autoInstallOnAppQuit).toBe(false);
    expect(fs.existsSync(attemptFile())).toBe(false);
  });

  test("on a nearly full disk, a quit leaves the update for later", async () => {
    electronMock.net = { isOnline: () => true };
    const statfs = jest.spyOn(fs, "statfsSync").mockReturnValue({ bavail: 100, bsize: 1024 * 1024 });
    await quitWith();
    expect(mockUpdater.autoInstallOnAppQuit).toBe(false);
    statfs.mockRestore();
  });

  test("a quit the guard cancelled notes no install", async () => {
    electronMock.net = { isOnline: () => true };
    await quitWith({ defaultPrevented: true });
    expect(fs.existsSync(attemptFile())).toBe(false);
  });

  test("after a failed silent install, a quit won't retry where nothing shows", () => {
    fs.mkdirSync(path.dirname(attemptFile()), { recursive: true });
    fs.writeFileSync(attemptFile(), JSON.stringify({ from: "0.25.0", target: "0.26.0" }));
    saveConfig({ autoUpdate: true });
    initUpdater({ confirmRestart: async () => true });
    expect(mockUpdater.autoInstallOnAppQuit).toBe(false);
  });
});
