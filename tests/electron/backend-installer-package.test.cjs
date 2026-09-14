// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

const fs = require("fs");
const path = require("path");
const vm = require("vm");
const { EventEmitter } = require("events");

const installerPath = path.resolve(__dirname, "../../src/gaia/apps/webui/services/backend-installer.cjs");

function loadInstaller(platform, localWheel) {
  const spawn = jest.fn(() => {
    const child = new EventEmitter();
    child.stdout = new EventEmitter();
    child.stderr = new EventEmitter();
    queueMicrotask(() => child.emit("exit", 0));
    return child;
  });
  const fakeFs = {
    ...fs,
    existsSync: (file) => String(file).includes("/venv"),
    mkdirSync: jest.fn(),
    writeFileSync: jest.fn(),
  };
  const context = {
    module: { exports: {} },
    __dirname: path.dirname(installerPath),
    console: { log: jest.fn(), error: jest.fn(), warn: jest.fn() },
    process: { platform, arch: "x64", env: localWheel ? { GAIA_LOCAL_WHEEL: localWheel } : {} },
    require: (name) => {
      if (name === "fs") return fakeFs;
      if (name === "path") return path.posix;
      if (name === "os") return { ...require("os"), homedir: () => "/fixture" };
      if (name === "child_process") return {
        spawn,
        execSync: jest.fn(),
        spawnSync: jest.fn(() => ({ status: 0, stdout: "GAIA version 0.23.1" })),
      };
      return require(name);
    },
  };
  vm.runInNewContext(fs.readFileSync(installerPath, "utf8"), context, { filename: installerPath });
  return { installer: context.module.exports, spawn };
}

describe("backend installation package sources", () => {
  test.each(["linux", "darwin"])("%s local wheels keep CPU-only PyTorch dependencies", async (platform) => {
    const { installer, spawn } = loadInstaller(platform, "/work/amd_gaia-0.23.1-py3-none-any.whl");
    await installer.installBackend({ skipGaiaInit: true, isPackaged: false });

    const [command, args] = spawn.mock.calls.find(([, argv]) => argv[0] === "pip");
    expect(command).toBe("uv");
    expect(args).toContain("/work/amd_gaia-0.23.1-py3-none-any.whl[ui]");
    expect(args.slice(-2)).toEqual(["--extra-index-url", "https://download.pytorch.org/whl/cpu"]);
  });

  test("Linux PyPI installs retain the CPU-only index", async () => {
    const { installer, spawn } = loadInstaller("linux");
    await installer.installBackend({ version: "0.23.1", skipGaiaInit: true, isPackaged: false });
    const [, args] = spawn.mock.calls.find(([, argv]) => argv[0] === "pip");
    expect(args).toContain("amd-gaia[ui]==0.23.1");
    expect(args).toContain("https://download.pytorch.org/whl/cpu");
  });

  test("Windows keeps its existing dependency source", async () => {
    const { installer, spawn } = loadInstaller("win32", "/work/amd_gaia-0.23.1-py3-none-any.whl");
    await installer.installBackend({ skipGaiaInit: true, isPackaged: false });
    const [, args] = spawn.mock.calls.find(([, argv]) => argv[0] === "pip");
    expect(args).not.toContain("--extra-index-url");
  });
});
