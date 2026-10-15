// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

// "Installed" must mean `gaia init` finished, not that the venv exists: an
// install interrupted during setup used to report "already installed" on the
// next launch and never set up Lemonade or the models.

const fs = require("fs");
const path = require("path");
const vm = require("vm");
const { EventEmitter } = require("events");

const installerPath = path.resolve(
  __dirname,
  "../../src/gaia/apps/webui/services/backend-installer.cjs"
);

function loadInstaller(previousState) {
  const files = new Map();
  if (previousState) {
    files.set("/fixture/.gaia/electron-install-state.json", JSON.stringify(previousState));
  }
  const spawn = jest.fn(() => {
    const child = new EventEmitter();
    child.stdout = new EventEmitter();
    child.stderr = new EventEmitter();
    queueMicrotask(() => child.emit("exit", 0));
    return child;
  });
  const fakeFs = {
    ...fs,
    existsSync: (file) => String(file).includes("/venv") || files.has(String(file)),
    readFileSync: (file, enc) =>
      files.has(String(file)) ? files.get(String(file)) : fs.readFileSync(file, enc),
    writeFileSync: (file, data) => files.set(String(file), String(data)),
    mkdirSync: jest.fn(),
    statfsSync: () => ({ bavail: 1e9, bsize: 4096 }),
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

describe("an install whose setup never finished", () => {
  test("interrupted during gaia init: the next launch runs gaia init, not 'already installed'", async () => {
    const { installer, spawn, state } = loadInstaller({
      state: "installing",
      stage: "gaia-init",
      version: "0.25.0",
    });

    await installer.ensureBackend({ version: "0.25.0", isPackaged: false });

    expect(initRuns(spawn)).toHaveLength(1);
    expect(initRuns(spawn)[0][1]).toEqual(
      expect.arrayContaining(["--profile", "gaia", "--yes"])
    );
    // The package was already in place: setup resumes, nothing reinstalls.
    expect(pipRuns(spawn)).toHaveLength(0);
    expect(state()).toMatchObject({ state: "ready", init: "done" });
  });

  test("a setup that failed last time runs again", async () => {
    const { installer, spawn, state } = loadInstaller({
      state: "ready",
      version: "0.25.0",
      init: "failed",
    });

    await installer.ensureBackend({ version: "0.25.0", isPackaged: false });

    expect(initRuns(spawn)).toHaveLength(1);
    expect(state()).toMatchObject({ state: "ready", init: "done" });
  });

  test("interrupted while installing the package: it reinstalls, then sets up", async () => {
    const { installer, spawn } = loadInstaller({
      state: "installing",
      stage: "install-package",
      version: "0.25.0",
    });

    await installer.ensureBackend({ version: "0.25.0", isPackaged: false });

    expect(pipRuns(spawn).length).toBeGreaterThan(0);
    expect(initRuns(spawn)).toHaveLength(1);
  });
});

describe("a finished install", () => {
  test("is ready without running anything", async () => {
    const { installer, spawn } = loadInstaller({
      state: "ready",
      version: "0.25.0",
      init: "done",
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
