// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

// A packaged app runs the frozen `gaia` it ships and never installs a Python
// package; without that executable it says so instead of reaching for PyPI.

const fs = require("fs");
const path = require("path");
const vm = require("vm");
const { EventEmitter } = require("events");

const installerPath = path.resolve(__dirname, "../../src/gaia/apps/webui/services/backend-installer.cjs");
const CORE = "/app/resources/core/gaia";

function load({ shipsCore = true, flagship = false, env = {}, files: given = {} } = {}) {
  const files = new Map(Object.entries(given));
  if (shipsCore) files.set(CORE, "");
  if (flagship) files.set("/fixture/.gaia/agents/gaia/.installed", "{}");
  const spawn = jest.fn(() => {
    const child = new EventEmitter();
    child.stdout = new EventEmitter();
    child.stderr = new EventEmitter();
    queueMicrotask(() => child.emit("exit", 0));
    return child;
  });
  const spawnSync = jest.fn(() => ({ status: 0, stdout: "" }));
  const fakeFs = {
    ...fs,
    existsSync: (f) => files.has(String(f)),
    readFileSync: (f, enc) => {
      if (files.has(String(f))) return files.get(String(f));
      if (String(f).endsWith("package.json")) return JSON.stringify({ version: "0.25.4-rc.3" });
      throw Object.assign(new Error(`ENOENT ${f}`), { code: "ENOENT" });
    },
    readdirSync: (d) => [
      ...new Set(
        [...files.keys()]
          .filter((f) => f.startsWith(`${d}/`))
          .map((f) => f.slice(String(d).length + 1).split("/")[0])
      ),
    ],
    rmSync: (target) => {
      for (const f of [...files.keys()]) {
        if (f === String(target) || f.startsWith(`${target}/`)) files.delete(f);
      }
    },
    promises: {
      cp: jest.fn(async (from, to) => {
        for (const f of [...files.keys()]) {
          if (f.startsWith(`${from}/`)) files.set(`${to}${f.slice(String(from).length)}`, files.get(f));
        }
      }),
    },
    writeFileSync: (f, d) => files.set(String(f), String(d)),
    mkdirSync: jest.fn(),
    statfsSync: () => ({ bavail: 1e9, bsize: 4096 }),
    createWriteStream: () => ({ write: jest.fn(), end: jest.fn(), on: jest.fn() }),
  };
  const network = jest.fn(() => {
    throw new Error("the network must not be used");
  });
  const context = {
    module: { exports: {} },
    __dirname: path.dirname(installerPath),
    console: { log: jest.fn(), error: jest.fn(), warn: jest.fn() },
    process: { platform: "linux", arch: "x64", env, resourcesPath: "/app/resources" },
    setTimeout, clearTimeout, queueMicrotask, URL,
    require: (n) => {
      if (n === "fs") return fakeFs;
      if (n === "path") return path.posix;
      if (n === "os") return { ...require("os"), homedir: () => "/fixture" };
      if (n === "https" || n === "http") return { request: network, get: network };
      if (n === "child_process") return { spawn, execFileSync: jest.fn(), spawnSync };
      return require(n);
    },
  };
  vm.runInNewContext(fs.readFileSync(installerPath, "utf8"), context, { filename: installerPath });
  const state = () => JSON.parse(files.get("/fixture/.gaia/electron-install-state.json") || "null");
  return { installer: context.module.exports, spawn, spawnSync, network, state, files, fakeFs };
}

describe("a packaged app's bundled core", () => {
  test("is the backend, with no install step at all", async () => {
    const { installer, spawn, network, state } = load();
    await expect(installer.ensureBackend({ isPackaged: true })).resolves.toBe(CORE);
    expect(installer.findGaiaBin()).toBe(CORE);
    expect(spawn).not.toHaveBeenCalled();
    expect(network).not.toHaveBeenCalled();
    expect(state()).toMatchObject({ state: "ready", installedVersion: "0.25.4rc3" });
    expect(installer.isBackendCurrent()).toBe(true);
  });

  test("after an app update, refreshes the agent once for the new version", async () => {
    const ready = { state: "ready", version: "0.25.4-rc.3" };
    const older = { state: "ready", version: "0.25.3" };
    const stateFile = "/fixture/.gaia/electron-install-state.json";
    expect(load({ flagship: true, files: { [stateFile]: JSON.stringify(older) } }).installer.isBackendCurrent()).toBe(false);
    expect(load({ flagship: true, files: { [stateFile]: JSON.stringify(ready) } }).installer.isBackendCurrent()).toBe(true);
    // A fresh install has no state: current unless an agent is waiting to be refreshed.
    expect(load({ flagship: true }).installer.isBackendCurrent()).toBe(false);
    expect(load().installer.isBackendCurrent()).toBe(true);
  });

  test("a fresh install stays current once first-run setup installs the agent", () => {
    const { installer, files, spawn, state } = load();
    expect(installer.isBackendCurrent()).toBe(true);
    expect(state()).toMatchObject({ state: "ready", version: "0.25.4-rc.3" });
    files.set("/fixture/.gaia/agents/gaia/.installed", "{}");
    expect(installer.isBackendCurrent()).toBe(true);
    expect(spawn).not.toHaveBeenCalled();
  });

  test("still updates an installed agent through the core", async () => {
    const { installer, spawn } = load({ flagship: true });
    await installer.ensureBackend({ isPackaged: true });
    expect(spawn).toHaveBeenCalledWith(CORE, ["init", "--agent-only"], expect.anything());
  });

  test("wins over a local wheel meant for a source run", async () => {
    const { installer } = load({ env: { GAIA_LOCAL_WHEEL: "/tmp/amd_gaia.whl" } });
    expect(installer.isBackendCurrent()).toBe(true);
    await expect(installer.ensureBackend({ isPackaged: true })).resolves.toBe(CORE);
  });

  test("leaves an app update nothing to prefetch", async () => {
    const { installer, spawn, network } = load();
    await installer.prefetchBackend("0.25.5", { isPackaged: true });
    expect(spawn).not.toHaveBeenCalled();
    expect(network).not.toHaveBeenCalled();
    // The updater lets an offline PC restart only when nothing is left to download.
    expect(installer.isBackendPrefetched("0.25.5")).toBe(true);
  });

  test("missing from a packaged app is an error, not a PyPI install", async () => {
    const { installer, spawn, network, state } = load({ shipsCore: false });
    await expect(installer.ensureBackend({ isPackaged: true })).rejects.toThrow(
      "This GAIA install is missing its backend."
    );
    expect(spawn).not.toHaveBeenCalled();
    expect(network).not.toHaveBeenCalled();
    expect(state()).toMatchObject({ state: "failed" });
  });

  test("in an AppImage runs from a copy that outlives the app's mount", async () => {
    const copy = "/fixture/.gaia/app-core/0.25.4rc3/gaia";
    const { installer, files, fakeFs } = load({
      env: { APPIMAGE: "/home/u/GAIA.AppImage" },
      files: {
        "/app/resources/core/_internal/libpython3.12.so": "",
        "/fixture/.gaia/app-core/0.25.3/gaia": "",
        // The one-line installer's own gaia, which the AppImage must leave alone.
        "/fixture/.gaia/core/gaia": "",
      },
    });
    expect(installer.isBackendCurrent()).toBe(false);
    await expect(installer.ensureBackend({ isPackaged: true })).resolves.toBe(copy);
    expect(files.has("/fixture/.gaia/app-core/0.25.4rc3/_internal/libpython3.12.so")).toBe(true);
    expect(files.has("/fixture/.gaia/app-core/0.25.3/gaia")).toBe(false);
    expect(files.has("/fixture/.gaia/core/gaia")).toBe(true);
    expect(installer.findGaiaBin()).toBe(copy);
    expect(installer.isBackendCurrent()).toBe(true);
    await installer.ensureBackend({ isPackaged: true });
    expect(fakeFs.promises.cp).toHaveBeenCalledTimes(1);
  });
});
