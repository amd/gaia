// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

const fs = require("fs");
const path = require("path");
const vm = require("vm");
const { EventEmitter } = require("events");

const installerPath = path.resolve(__dirname, "../../src/gaia/apps/webui/services/backend-installer.cjs");

function load(previousState, { flagship = false, env = {}, appVersion = "0.25.0", exitCode = 0, stderr = "" } = {}) {
  const files = new Map();
  if (previousState) files.set("/fixture/.gaia/electron-install-state.json", JSON.stringify(previousState));
  if (flagship) files.set("/fixture/.gaia/agents/gaia/.installed", "{}");
  const spawn = jest.fn(() => {
    const child = new EventEmitter();
    child.stdout = new EventEmitter();
    child.stderr = new EventEmitter();
    queueMicrotask(() => {
      if (stderr) child.stderr.emit("data", Buffer.from(stderr));
      child.emit("exit", exitCode);
    });
    return child;
  });
  const fakeFs = {
    ...fs,
    existsSync: (f) => String(f).includes("/venv") || files.has(String(f)),
    readFileSync: (f, enc) => {
      if (files.has(String(f))) return files.get(String(f));
      if (String(f).endsWith("package.json")) return JSON.stringify({ version: appVersion });
      if (String(f).endsWith("electron-install.log")) throw Object.assign(new Error("x"), { code: "ENOENT" });
      return fs.readFileSync(f, enc);
    },
    readdirSync: (d) =>
      ({
        "/fixture/.gaia/venv/lib": ["python3.12"],
        "/fixture/.gaia/venv/lib/python3.12/site-packages": ["amd_gaia-0.25.0.dist-info"],
      })[String(d)] || [],
    writeFileSync: (f, d) => files.set(String(f), String(d)),
    mkdirSync: jest.fn(),
    statfsSync: () => ({ bavail: 1e9, bsize: 4096 }),
    createWriteStream: () => ({ write: jest.fn(), end: jest.fn(), on: jest.fn() }),
  };
  const offline = {
    request: () => {
      const r = new EventEmitter();
      r.end = () => queueMicrotask(() => r.emit("error", new Error("offline")));
      r.destroy = jest.fn();
      return r;
    },
  };
  const context = {
    module: { exports: {} },
    __dirname: path.dirname(installerPath),
    console: { log: jest.fn(), error: jest.fn(), warn: jest.fn() },
    process: { platform: "linux", arch: "x64", env },
    setTimeout, clearTimeout, queueMicrotask, URL,
    require: (n) => {
      if (n === "fs") return fakeFs;
      if (n === "path") return path.posix;
      if (n === "os") return { ...require("os"), homedir: () => "/fixture" };
      if (n === "https") return offline;
      if (n === "child_process") {
        return { spawn, execSync: jest.fn(), spawnSync: jest.fn(() => ({ status: 0, stdout: "GAIA version 0.25.0" })) };
      }
      return require(n);
    },
  };
  vm.runInNewContext(fs.readFileSync(installerPath, "utf8"), context, { filename: installerPath });
  const state = () => JSON.parse(files.get("/fixture/.gaia/electron-install-state.json") || "null");
  return { installer: context.module.exports, spawn, state };
}

describe("the agent update after an app update", () => {
  test("a recorded update with no agent to update is done, not shown on every launch", async () => {
    const { installer, state } = load({ state: "failed", stage: "agent-refresh", version: "0.25.0" }, { flagship: false });
    await installer.ensureBackend({ isPackaged: false });
    expect(state()).toMatchObject({ state: "ready" });
    expect(installer.isBackendCurrent()).toBe(true);
  });

  test("offline, the working agent stays and the update is tried again next launch", async () => {
    const { installer, state, spawn } = load(
      { state: "failed", stage: "agent-refresh", version: "0.25.0" },
      { flagship: true, exitCode: 5, stderr: "Error: Could not compare the 'gaia' agent with the download server: offline" },
    );
    await expect(installer.ensureBackend({ isPackaged: false })).resolves.toBeTruthy();
    expect(spawn).toHaveBeenCalledWith(expect.anything(), ["init", "--agent-only"], expect.anything());
    expect(state()).toMatchObject({ state: "partial", stage: "agent-refresh" });
    expect(installer.isBackendCurrent()).toBe(false);
  });

  test("a failed update still fails", async () => {
    const { installer } = load(
      { state: "failed", stage: "agent-refresh", version: "0.25.0" },
      { flagship: true, exitCode: 1, stderr: "Error: disk full" },
    );
    await expect(installer.ensureBackend({ isPackaged: false })).rejects.toThrow("disk full");
  });
});

test("a local wheel is reinstalled on every launch", () => {
  const { installer } = load({ state: "ready", version: null }, { env: { GAIA_LOCAL_WHEEL: "/w/amd_gaia-0.25.0-py3-none-any.whl" } });
  expect(installer.isBackendCurrent()).toBe(false);
});
