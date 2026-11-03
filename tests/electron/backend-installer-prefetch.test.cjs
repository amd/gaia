// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

// The first launch after an app update installed its backend from PyPI, so it
// waited on the download and failed outright when PyPI was down. The update now
// fetches that backend into uv's cache before the restart, and the first
// launch installs it offline.

const fs = require("fs");
const os = require("os");
const path = require("path");
const vm = require("vm");
const { EventEmitter } = require("events");

const installerPath = path.resolve(
  __dirname,
  "../../src/gaia/apps/webui/services/backend-installer.cjs"
);

let home;

beforeEach(() => {
  home = fs.mkdtempSync(path.join(os.tmpdir(), "gaia-prefetch-")).replace(/\\/g, "/");
});

afterEach(() => {
  fs.rmSync(home, { recursive: true, force: true });
});

/** The installer with `uv` faked: `exitFor(argv)` picks each call's exit code. */
function loadInstaller(exitFor = () => 0) {
  const spawn = jest.fn((cmd, argv) => {
    const child = new EventEmitter();
    child.stdout = new EventEmitter();
    child.stderr = new EventEmitter();
    const code = exitFor(argv);
    if (code === 0 && argv[0] === "venv") {
      fs.mkdirSync(path.posix.join(argv[1], "bin"), { recursive: true });
      fs.writeFileSync(path.posix.join(argv[1], "bin", "python"), "");
    }
    if (code === 0 && argv[0] === "pip") {
      const python = argv[argv.indexOf("--python") + 1];
      fs.writeFileSync(path.posix.join(path.posix.dirname(python), "gaia"), "");
    }
    queueMicrotask(() => {
      if (code !== 0) child.stderr.emit("data", Buffer.from("error: No solution found\n"));
      child.emit("exit", code);
    });
    return child;
  });
  const context = {
    module: { exports: {} },
    __dirname: path.dirname(installerPath),
    console: { log: jest.fn(), error: jest.fn(), warn: jest.fn() },
    process: { platform: "linux", arch: "x64", env: {} },
    require: (name) => {
      if (name === "path") return path.posix;
      if (name === "os") return { ...os, homedir: () => home };
      if (name === "child_process")
        return {
          spawn,
          execSync: jest.fn(),
          spawnSync: jest.fn(() => ({ status: 0, stdout: "0.26.0" })),
        };
      return require(name);
    },
  };
  vm.runInNewContext(fs.readFileSync(installerPath, "utf8"), context, { filename: installerPath });
  return { installer: context.module.exports, spawn };
}

const pipCalls = (spawn) => spawn.mock.calls.filter(([, argv]) => argv[0] === "pip").map(([, a]) => a);
const prefetchDir = () => path.posix.join(home, ".gaia", "cache", "backend-prefetch");

describe("fetching an update's backend before the restart", () => {
  test("a release candidate's backend lands in uv's cache and the scratch venv goes", async () => {
    const { installer, spawn } = loadInstaller();
    await installer.prefetchBackend("0.26.0-rc.2");
    const [pip] = pipCalls(spawn);
    expect(pip.slice(0, 4)).toEqual(["pip", "install", "amd-gaia[ui]==0.26.0rc2", "--refresh"]);
    expect(installer.isBackendPrefetched("0.26.0-rc.2")).toBe(true);
    expect(fs.readdirSync(prefetchDir())).toEqual(["0.26.0rc2.ready"]);
  });

  test("a download that fails says why and leaves nothing marked ready", async () => {
    const { installer } = loadInstaller((argv) => (argv[0] === "pip" ? 1 : 0));
    await expect(installer.prefetchBackend("0.26.0")).rejects.toThrow(
      /Couldn't download amd-gaia\[ui\]==0.26.0 .*No solution found/
    );
    expect(installer.isBackendPrefetched("0.26.0")).toBe(false);
    expect(fs.readdirSync(prefetchDir())).toEqual([]);
  });
});

describe("the first launch after a prefetched update", () => {
  test("installs offline, without asking PyPI", async () => {
    const { installer, spawn } = loadInstaller();
    await installer.prefetchBackend("0.26.0");
    spawn.mockClear();

    await installer.installBackend({ version: "0.26.0", skipGaiaInit: true, isPackaged: false });

    const pips = pipCalls(spawn);
    expect(pips).toHaveLength(1);
    expect(pips[0]).toContain("--offline");
    expect(pips[0]).not.toContain("--refresh");
    expect(installer.isBackendPrefetched("0.26.0")).toBe(false);
  });

  test("a cache that lost the packages installs from PyPI instead of failing", async () => {
    const { installer, spawn } = loadInstaller((argv) => (argv.includes("--offline") ? 1 : 0));
    await installer.prefetchBackend("0.26.0");
    spawn.mockClear();

    await installer.installBackend({ version: "0.26.0", skipGaiaInit: true, isPackaged: false });

    const pips = pipCalls(spawn);
    expect(pips).toHaveLength(2);
    expect(pips[1]).toContain("--refresh");
    expect(pips[1]).not.toContain("--offline");
  });

  test("with nothing prefetched the install goes to PyPI as before", async () => {
    const { installer, spawn } = loadInstaller();
    await installer.installBackend({ version: "0.26.0", skipGaiaInit: true, isPackaged: false });
    const pips = pipCalls(spawn);
    expect(pips).toHaveLength(1);
    expect(pips[0]).toContain("--refresh");
  });
});
