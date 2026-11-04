// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

// The installer installs the app and its Python backend, and downloads no
// model: `gaia init` (Lemonade and the models) belongs to the app's first-run
// setup, where the user chooses cloud or local and sees the download (#5629).
// "Installed" therefore means the package is in place at the app's version.

const fs = require("fs");
const path = require("path");
const vm = require("vm");
const { EventEmitter } = require("events");

const installerPath = path.resolve(
  __dirname,
  "../../src/gaia/apps/webui/services/backend-installer.cjs"
);

function loadInstaller(
  previousState,
  {
    freeBytes = 1e9 * 4096,
    flagship = false,
    refresh = { code: 0 },
    appVersion = null,
    venvVersion = "0.25.0",
  } = {}
) {
  const files = new Map();
  if (previousState) {
    files.set("/fixture/.gaia/electron-install-state.json", JSON.stringify(previousState));
  }
  if (flagship) files.set("/fixture/.gaia/agents/gaia/.installed", "{}");
  const spawn = jest.fn((cmd, argv) => {
    const child = new EventEmitter();
    child.stdout = new EventEmitter();
    child.stderr = new EventEmitter();
    const refreshing = argv[0] === "init";
    queueMicrotask(() => {
      if (refreshing && refresh.stderr) child.stderr.emit("data", Buffer.from(refresh.stderr));
      child.emit("exit", refreshing ? refresh.code : 0);
    });
    return child;
  });
  const fakeFs = {
    ...fs,
    existsSync: (file) => String(file).includes("/venv") || files.has(String(file)),
    readFileSync: (file, enc) => {
      if (files.has(String(file))) return files.get(String(file));
      if (appVersion && String(file).endsWith("package.json")) {
        return JSON.stringify({ version: appVersion });
      }
      if (String(file) === "/fixture/.gaia/electron-install.log") {
        throw Object.assign(new Error("no log"), { code: "ENOENT" });
      }
      return fs.readFileSync(file, enc);
    },
    readdirSync: (dir) =>
      ({
        "/fixture/.gaia/venv/lib": ["python3.12"],
        "/fixture/.gaia/venv/lib/python3.12/site-packages": [`amd_gaia-${venvVersion}.dist-info`],
      })[String(dir)] || [],
    writeFileSync: (file, data) => files.set(String(file), String(data)),
    mkdirSync: jest.fn(),
    statfsSync: () => ({ bavail: freeBytes / 4096, bsize: 4096 }),
    createWriteStream: () => ({ write: jest.fn(), end: jest.fn(), on: jest.fn() }),
  };
  const offline = {
    request: () => {
      const req = new EventEmitter();
      req.end = () => queueMicrotask(() => req.emit("error", new Error("offline")));
      req.destroy = jest.fn();
      return req;
    },
  };
  const context = {
    module: { exports: {} },
    __dirname: path.dirname(installerPath),
    console: { log: jest.fn(), error: jest.fn(), warn: jest.fn() },
    process: { platform: "linux", arch: "x64", env: {} },
    setTimeout,
    clearTimeout,
    queueMicrotask,
    URL,
    require: (name) => {
      if (name === "fs") return fakeFs;
      if (name === "path") return path.posix;
      if (name === "os") return { ...require("os"), homedir: () => "/fixture" };
      if (name === "https") return offline;
      if (name === "child_process") {
        return {
          spawn,
          execSync: jest.fn(),
          spawnSync: jest.fn(() => ({ status: 0, stdout: "GAIA version 0.25.0" })),
        };
      }
      return require(name);
    },
  };
  vm.runInNewContext(fs.readFileSync(installerPath, "utf8"), context, {
    filename: installerPath,
  });
  const state = () =>
    JSON.parse(files.get("/fixture/.gaia/electron-install-state.json") || "null");
  return { installer: context.module.exports, spawn, state };
}

const initRuns = (spawn) => spawn.mock.calls.filter(([, argv]) => argv[0] === "init");
const pipRuns = (spawn) => spawn.mock.calls.filter(([, argv]) => argv[0] === "pip");

describe("the installer downloads no model", () => {
  test("a fresh install never runs gaia init", async () => {
    const { installer, spawn, state } = loadInstaller(null);
    const progress = [];

    await installer.installBackend({
      version: "0.25.0",
      isPackaged: false,
      onProgress: (stage, percent, message) => progress.push({ stage, percent, message }),
    });

    expect(pipRuns(spawn).length).toBeGreaterThan(0);
    expect(initRuns(spawn)).toHaveLength(0);
    expect(progress.some((p) => /model/i.test(p.message))).toBe(false);
    expect(progress[progress.length - 1].percent).toBe(100);
    expect(state()).toMatchObject({ state: "ready" });
  });

  test("an install stopped where an older installer ran gaia init is ready, with nothing re-run", async () => {
    const { installer, spawn, state } = loadInstaller({
      state: "installing",
      stage: "gaia-init",
      version: "0.25.0",
    });

    await installer.ensureBackend({ version: "0.25.0", isPackaged: false });

    expect(spawn).not.toHaveBeenCalled();
    expect(state()).toMatchObject({ state: "ready" });
  });

  test("a setup an older installer failed is left to the app's first-run setup", async () => {
    const { installer, spawn, state } = loadInstaller({
      state: "ready",
      version: "0.25.0",
      init: "failed",
    });

    await installer.ensureBackend({ version: "0.25.0", isPackaged: false });

    expect(spawn).not.toHaveBeenCalled();
    expect(state()).toMatchObject({ state: "ready" });
  });

  test("interrupted while installing the package: it reinstalls, and still sets nothing up", async () => {
    const { installer, spawn } = loadInstaller({
      state: "installing",
      stage: "install-package",
      version: "0.25.0",
    });

    await installer.ensureBackend({ version: "0.25.0", isPackaged: false });

    expect(pipRuns(spawn).length).toBeGreaterThan(0);
    expect(initRuns(spawn)).toHaveLength(0);
  });

  test("the disk check asks only for room for the Python backend", async () => {
    const { installer } = loadInstaller(null, { freeBytes: 3 * 1024 ** 3 });

    const report = await installer.runPreChecks({});

    expect(report.disk.ok).toBe(true);
    expect(installer.MIN_DISK_SPACE_BYTES).toBeLessThan(3 * 1024 ** 3);
  });
});

describe("a finished install", () => {
  test("is ready without running anything", async () => {
    const { installer, spawn } = loadInstaller({
      state: "ready",
      version: "0.25.0",
    });

    await installer.ensureBackend({ version: "0.25.0", isPackaged: false });

    expect(spawn).not.toHaveBeenCalled();
  });

  test("an install from before the state file existed is left alone", async () => {
    const { installer, spawn } = loadInstaller(null);

    await installer.ensureBackend({ version: "0.25.0", isPackaged: false });

    expect(spawn).not.toHaveBeenCalled();
  });
});

const AGENT_ONLY = ["init", "--agent-only"];

describe("the agent refresh after an update", () => {
  const OLD = { state: "ready", version: "0.24.0" };

  test("an update refreshes the installed agent, after the package, with no model step", async () => {
    const { installer, spawn, state } = loadInstaller(OLD, { flagship: true });
    const progress = [];

    await installer.ensureBackend({
      version: "0.25.1",
      isPackaged: false,
      onProgress: (stage, percent) => progress.push({ stage, percent }),
    });

    const calls = spawn.mock.calls.map(([, argv]) => argv);
    expect(initRuns(spawn).map(([, argv]) => [...argv])).toEqual([AGENT_ONLY]);
    expect(calls.findIndex((a) => a[0] === "init")).toBeGreaterThan(
      calls.findLastIndex((a) => a[0] === "pip")
    );
    expect(progress[progress.length - 1]).toEqual({ stage: "agent-refresh", percent: null });
    expect(state()).toMatchObject({ state: "ready" });
  });

  test("a first install leaves the agent to first-run setup", async () => {
    const { installer, spawn } = loadInstaller(null);

    await installer.ensureBackend({ version: "0.25.1", isPackaged: false });

    expect(pipRuns(spawn).length).toBeGreaterThan(0);
    expect(initRuns(spawn)).toHaveLength(0);
  });

  test("a launch with nothing to update does not touch the agent", async () => {
    const { installer, spawn } = loadInstaller({ state: "ready", version: "0.25.0" }, { flagship: true });

    await installer.ensureBackend({ version: "0.25.0", isPackaged: false });

    expect(spawn).not.toHaveBeenCalled();
  });

  test("a failed refresh throws its own cause and stays on record", async () => {
    const { installer, state } = loadInstaller(OLD, {
      flagship: true,
      refresh: { code: 1, stderr: "some warning\nError: Could not reach the hub: timed out\n" },
    });

    await expect(installer.ensureBackend({ version: "0.25.1", isPackaged: false })).rejects.toMatchObject({
      stage: "agent-refresh",
      message: "Could not reach the hub: timed out",
    });

    expect(state()).toMatchObject({ state: "failed", stage: "agent-refresh" });
  });

  test.each([
    ["failed", { state: "failed", stage: "agent-refresh", version: "0.25.0" }],
    ["quit part way", { state: "installing", stage: "agent-refresh", version: "0.25.0" }],
  ])("a refresh that %s runs again without reinstalling the package", async (_name, previous) => {
    const { installer, spawn, state } = loadInstaller(previous, { flagship: true });

    await installer.ensureBackend({ version: "0.25.0", isPackaged: false });

    expect(pipRuns(spawn)).toHaveLength(0);
    expect(initRuns(spawn).map(([, argv]) => [...argv])).toEqual([AGENT_ONLY]);
    expect(state()).toMatchObject({ state: "ready" });
  });
});

describe("whether a launch needs the install screen", () => {
  test("a finished install at the app's version does not", () => {
    const { installer } = loadInstaller({ state: "ready" }, { appVersion: "0.25.0" });
    expect(installer.isBackendCurrent()).toBe(true);
  });

  test("an install from before the state file existed does not", () => {
    const { installer } = loadInstaller(null, { appVersion: "0.25.0" });
    expect(installer.isBackendCurrent()).toBe(true);
  });

  test("a release candidate's backend matches in its PEP 440 spelling", () => {
    const { installer } = loadInstaller(
      { state: "ready" },
      { appVersion: "0.25.0-rc.1", venvVersion: "0.25.0rc1" }
    );
    expect(installer.isBackendCurrent()).toBe(true);
  });

  test("an update does", () => {
    const { installer } = loadInstaller({ state: "ready" }, { appVersion: "0.25.1" });
    expect(installer.isBackendCurrent()).toBe(false);
  });

  test.each([
    ["a failed install", { state: "failed", stage: "install-package", message: "pip exit 1" }],
    ["a failed agent refresh", { state: "failed", stage: "agent-refresh" }],
    ["an interrupted install", { state: "installing", stage: "install-package" }],
    ["a partial install", { state: "partial", stage: "install-package" }],
  ])("%s does, and its record survives the check", (_name, previous) => {
    const { installer, state } = loadInstaller(previous, { appVersion: "0.25.0" });

    expect(installer.isBackendCurrent()).toBe(false);
    expect(state()).toMatchObject(previous);
  });
});

describe("readLogTail", () => {
  test("an install that wrote no log yet has no tail", () => {
    const { installer } = loadInstaller(null);
    expect(installer.readLogTail()).toEqual([]);
  });
});
