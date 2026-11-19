// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

// On a PC with no Python 3.12, `uv venv` downloads one. Behind a corporate
// TLS-inspection proxy that download fails on uv's bundled roots, so the
// first-run install died at "create-venv" even though the pip step already
// knew to retry with --native-tls.

const fs = require("fs");
const path = require("path");
const vm = require("vm");
const { EventEmitter } = require("events");

const installerPath = path.resolve(
  __dirname,
  "../../src/gaia/apps/webui/services/backend-installer.cjs"
);

const TLS_FAILURE =
  "error: Failed to download https://github.com/astral-sh/python-build-standalone/...\n" +
  "  Caused by: invalid peer certificate: UnknownIssuer\n";

function loadInstaller(venvExit) {
  let venvCreated = false;
  const spawn = jest.fn((cmd, argv) => {
    const child = new EventEmitter();
    child.stdout = new EventEmitter();
    child.stderr = new EventEmitter();
    let code = 0;
    if (argv[0] === "venv") {
      code = venvExit(argv);
      if (code === 0) venvCreated = true;
    }
    queueMicrotask(() => {
      if (code !== 0) child.stderr.emit("data", Buffer.from(TLS_FAILURE));
      child.emit("exit", code);
    });
    return child;
  });
  const fakeFs = {
    ...fs,
    existsSync: (file) => venvCreated && String(file).includes("/venv"),
    mkdirSync: jest.fn(),
    writeFileSync: jest.fn(),
    appendFileSync: jest.fn(),
    rmSync: jest.fn(),
  };
  const context = {
    module: { exports: {} },
    __dirname: path.dirname(installerPath),
    console: { log: jest.fn(), error: jest.fn(), warn: jest.fn() },
    process: { platform: "linux", arch: "x64", env: {} },
    require: (name) => {
      if (name === "fs") return fakeFs;
      if (name === "path") return path.posix;
      if (name === "os") return { ...require("os"), homedir: () => "/fixture" };
      if (name === "child_process")
        return {
          spawn,
          execSync: jest.fn(),
          spawnSync: jest.fn(() => ({ status: 0, stdout: "0.25.0" })),
        };
      return require(name);
    },
  };
  vm.runInNewContext(fs.readFileSync(installerPath, "utf8"), context, {
    filename: installerPath,
  });
  return { installer: context.module.exports, spawn };
}

const venvCalls = (spawn) =>
  spawn.mock.calls.filter(([, argv]) => argv[0] === "venv").map(([, a]) => a);

describe("creating the backend venv behind a TLS-inspecting proxy", () => {
  test("retries uv venv once with --native-tls after a certificate error", async () => {
    const { installer, spawn } = loadInstaller((argv) =>
      argv.includes("--native-tls") ? 0 : 2
    );
    await installer.installBackend({
      version: "0.25.0",
      isPackaged: false,
    });
    const calls = venvCalls(spawn);
    expect(calls).toHaveLength(2);
    expect(calls[0]).not.toContain("--native-tls");
    expect(calls[1]).toContain("--native-tls");
  });

  test("a failure that persists names the --native-tls command to try", async () => {
    const { installer, spawn } = loadInstaller(() => 2);
    await expect(
      installer.installBackend({
        version: "0.25.0",
        isPackaged: false,
      })
    ).rejects.toMatchObject({
      suggestion: expect.stringContaining("--python 3.12 --native-tls"),
    });
    expect(venvCalls(spawn)).toHaveLength(2);
  });
});
