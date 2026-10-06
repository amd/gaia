// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

const fs = require("fs");
const path = require("path");
const vm = require("vm");
const { EventEmitter } = require("events");

const installerPath = path.resolve(__dirname, "../../src/gaia/apps/webui/services/backend-installer.cjs");

function loadInstaller(platform, localWheel, versionOutput = "GAIA version 0.23.1") {
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
        spawnSync: jest.fn(() => ({ status: 0, stdout: versionOutput })),
      };
      return require(name);
    },
  };
  vm.runInNewContext(fs.readFileSync(installerPath, "utf8"), context, { filename: installerPath });
  return { installer: context.module.exports, spawn };
}

describe("backend installation package sources", () => {
  // [ui] carries no PyTorch, so no platform needs the CPU wheel index — and
  // the index pins its own `requests`, which once failed the whole install.
  test.each(["linux", "darwin", "win32"])("%s installs from PyPI alone", async (platform) => {
    const { installer, spawn } = loadInstaller(platform, "/work/amd_gaia-0.23.1-py3-none-any.whl");
    await installer.installBackend({ skipGaiaInit: true, isPackaged: false });

    const [command, args] = spawn.mock.calls.find(([, argv]) => argv[0] === "pip");
    expect(command).toBe("uv");
    expect(args).toContain("/work/amd_gaia-0.23.1-py3-none-any.whl[ui]");
    expect(args).not.toContain("--extra-index-url");
    expect(args).not.toContain("--index-strategy");
  });

  test("PyPI installs pin the matching backend version", async () => {
    const { installer, spawn } = loadInstaller("linux");
    await installer.installBackend({ version: "0.23.1", skipGaiaInit: true, isPackaged: false });
    const [, args] = spawn.mock.calls.find(([, argv]) => argv[0] === "pip");
    expect(args).toContain("amd-gaia[ui]==0.23.1");
    expect(args.join(" ")).not.toContain("download.pytorch.org");
  });
});

describe("release-candidate backend versions", () => {
  // The app carries npm's 0.25.0-rc.1; PyPI and `gaia --version` say 0.25.0rc1.
  test("an RC app pins the PEP 440 spelling of its version", async () => {
    const { installer, spawn } = loadInstaller("linux");
    await installer.installBackend({ version: "0.25.0-rc.1", skipGaiaInit: true, isPackaged: false });
    const [, args] = spawn.mock.calls.find(([, argv]) => argv[0] === "pip");
    expect(args).toContain("amd-gaia[ui]==0.25.0rc1");
  });

  test("a final version is unchanged", () => {
    const { installer } = loadInstaller("linux");
    expect(installer.toPep440("0.25.0")).toBe("0.25.0");
    expect(installer.toPep440("0.25.0-rc.12")).toBe("0.25.0rc12");
  });

  // Truncating to 0.25.0 would make an RC backend look like the final's, so
  // the final app would never upgrade it.
  test("the installed version keeps its rc suffix", () => {
    const { installer } = loadInstaller("linux", undefined, "0.25.0rc1");
    expect(installer.getInstalledVersion("/fixture/gaia")).toBe("0.25.0rc1");
  });
});
