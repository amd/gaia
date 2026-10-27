// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

// On a busy PC `gaia --version` can take 20 s+. The installed version must not
// read as "unknown" then, or every launch reinstalls the backend and re-runs
// `gaia init`, which also restarts the running agent (#5917).

const fs = require("fs");
const path = require("path");
const vm = require("vm");
const { EventEmitter } = require("events");

const installerPath = path.resolve(
  __dirname,
  "../../src/gaia/apps/webui/services/backend-installer.cjs"
);

const READY = { state: "ready", version: "0.25.2-rc.3", init: "done" };

// `sitePackages` maps a site-packages dir to the entries in it.
// `missing` lists paths that read as absent even though they sit under the venv.
function loadInstaller({ platform = "darwin", sitePackages = {}, venvLibs = ["python3.12"], missing = [] } = {}) {
  const posix = platform !== "win32";
  const p = posix ? path.posix : path.win32;
  const home = posix ? "/fixture" : "C:\\fixture";
  const venv = p.join(home, ".gaia", "venv");
  const files = new Map([[p.join(home, ".gaia", "electron-install-state.json"), JSON.stringify(READY)]]);
  const dirs = new Map(Object.entries(sitePackages));
  if (posix) dirs.set(p.join(venv, "lib"), venvLibs);

  const spawn = jest.fn(() => {
    const child = new EventEmitter();
    child.stdout = new EventEmitter();
    child.stderr = new EventEmitter();
    queueMicrotask(() => child.emit("exit", 0));
    return child;
  });
  // What spawnSync returns when its timeout kills a slow `gaia --version`.
  const spawnSync = jest.fn(() => ({ status: null, signal: "SIGTERM", stdout: "", error: new Error("spawnSync ETIMEDOUT") }));
  const fakeFs = {
    ...fs,
    existsSync: (file) =>
      !missing.includes(String(file)) &&
      (String(file).includes("venv") || files.has(String(file)) || dirs.has(String(file))),
    readFileSync: (file, enc) =>
      files.has(String(file)) ? files.get(String(file)) : fs.readFileSync(file, enc),
    readdirSync: (dir) => {
      if (!dirs.has(String(dir))) {
        throw Object.assign(new Error(`ENOENT: no such file or directory, scandir '${dir}'`), { code: "ENOENT" });
      }
      return dirs.get(String(dir));
    },
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
    process: { platform, arch: "arm64", env: {} },
    setTimeout,
    clearTimeout,
    queueMicrotask,
    URL,
    require: (name) => {
      if (name === "fs") return fakeFs;
      if (name === "path") return p;
      if (name === "os") return { ...require("os"), homedir: () => home };
      if (name === "https") return offline;
      if (name === "child_process") return { spawn, execSync: jest.fn(), spawnSync };
      return require(name);
    },
  };
  vm.runInNewContext(fs.readFileSync(installerPath, "utf8"), context, { filename: installerPath });
  return { installer: context.module.exports, spawn, spawnSync, venv };
}

const pipRuns = (spawn) => spawn.mock.calls.filter(([, argv]) => argv[0] === "pip");
const initRuns = (spawn) => spawn.mock.calls.filter(([, argv]) => argv[0] === "init");

describe("the installed backend version on a busy PC", () => {
  const macSite = "/fixture/.gaia/venv/lib/python3.12/site-packages";

  test("a slow `gaia --version` does not reinstall a backend that is already current", async () => {
    const { installer, spawn } = loadInstaller({
      sitePackages: { [macSite]: ["fastapi", "amd_gaia-0.25.2rc3.dist-info", "gaia"] },
    });

    await installer.ensureBackend({ version: "0.25.2-rc.3", isPackaged: false });

    expect(pipRuns(spawn)).toHaveLength(0);
    expect(initRuns(spawn)).toHaveLength(0);
  });

  test("a different installed version still upgrades", async () => {
    const { installer, spawn } = loadInstaller({
      sitePackages: { [macSite]: ["amd_gaia-0.25.1rc11.dist-info"] },
    });

    await installer.ensureBackend({ version: "0.25.2-rc.3", isPackaged: false });

    expect(pipRuns(spawn).length).toBeGreaterThan(0);
  });

  test("reads the Windows venv layout", () => {
    const { installer, venv } = loadInstaller({
      platform: "win32",
      sitePackages: { "C:\\fixture\\.gaia\\venv\\Lib\\site-packages": ["amd_gaia-0.25.2.dist-info"] },
    });

    expect(installer.getInstalledVersion(path.win32.join(venv, "Scripts", "gaia.exe"))).toBe("0.25.2");
  });

  test("a venv without the package reads as not installed", () => {
    const { installer, venv } = loadInstaller({ sitePackages: { [macSite]: ["fastapi"] } });

    expect(installer.getInstalledVersion(path.posix.join(venv, "bin", "gaia"))).toBeNull();
  });

  test("a venv whose Python is gone reinstalls even though the package record is still there", async () => {
    const { installer, spawn } = loadInstaller({
      sitePackages: { [macSite]: ["amd_gaia-0.25.2rc3.dist-info"] },
      missing: ["/fixture/.gaia/venv/bin/python"],
    });

    await installer.ensureBackend({ version: "0.25.2-rc.3", isPackaged: false });

    expect(pipRuns(spawn).length).toBeGreaterThan(0);
  });

  test("a Windows venv without python.exe reads as not installed", () => {
    const { installer, venv } = loadInstaller({
      platform: "win32",
      sitePackages: { "C:\\fixture\\.gaia\\venv\\Lib\\site-packages": ["amd_gaia-0.25.2.dist-info"] },
      missing: ["C:\\fixture\\.gaia\\venv\\Scripts\\python.exe"],
    });

    expect(installer.getInstalledVersion(path.win32.join(venv, "Scripts", "gaia.exe"))).toBeNull();
  });

  test("two package records read as not installed", () => {
    const { installer, venv } = loadInstaller({
      sitePackages: { [macSite]: ["amd_gaia-0.25.1.dist-info", "amd_gaia-0.25.2.dist-info"] },
    });

    expect(installer.getInstalledVersion(path.posix.join(venv, "bin", "gaia"))).toBeNull();
  });

  test("a gaia found on PATH still asks the binary itself", () => {
    const { installer, spawnSync } = loadInstaller({ sitePackages: { [macSite]: ["amd_gaia-0.25.2.dist-info"] } });

    expect(installer.getInstalledVersion("/usr/local/bin/gaia")).toBeNull();
    expect(spawnSync).toHaveBeenCalledWith("/usr/local/bin/gaia", ["--version"], expect.anything());
  });
});
