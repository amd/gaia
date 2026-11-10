// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

// Quitting during the backend install stops the step that is running, with
// every process it started, and leaves the install to run again next launch
// rather than recording it as failed (#6579).

"use strict";

const fs = require("fs");
const path = require("path");
const vm = require("vm");
const childProcess = require("child_process");

const servicesDir = path.resolve(__dirname, "../../src/gaia/apps/webui/services");
const installerPath = path.join(servicesDir, "backend-installer.cjs");
const FIXTURE = path.join(__dirname, "fixtures", "proc-tree-child.cjs");
const spawned = [];

jest.setTimeout(30000);

function pidAlive(pid) {
  try {
    process.kill(pid, 0);
    return true;
  } catch (err) {
    return err.code === "EPERM";
  }
}

async function waitFor(pred, ms = 10000) {
  const deadline = Date.now() + ms;
  while (Date.now() < deadline) {
    if (pred()) return true;
    await new Promise((r) => setTimeout(r, 25));
  }
  return pred();
}

/**
 * The installer with a recorded agent update pending, so ensureBackend's one
 * step is `gaia init --agent-only`. That step runs the process-tree fixture:
 * a real child with a real grandchild, which never exits on its own.
 */
function load() {
  const files = new Map([
    ["/fixture/.gaia/electron-install-state.json", JSON.stringify({ state: "failed", stage: "agent-refresh", version: "0.25.0" })],
    ["/fixture/.gaia/agents/gaia/.installed", "{}"],
  ]);
  const pids = [];
  const spawn = jest.fn((cmd, args, opts) => {
    const child = childProcess.spawn(process.execPath, [FIXTURE, "--children=1"], { ...opts, cwd: undefined, env: process.env });
    spawned.push(child);
    child.stdout.on("data", (d) => {
      for (const m of String(d).matchAll(/PID (\d+)/g)) {
        pids.push(Number(m[1]));
        spawned.push(Number(m[1]));
      }
    });
    return child;
  });
  const fakeFs = {
    ...fs,
    existsSync: (f) => String(f).includes("/venv") || files.has(String(f)),
    readFileSync: (f, enc) => {
      if (files.has(String(f))) return files.get(String(f));
      if (String(f).endsWith("package.json")) return JSON.stringify({ version: "0.25.0" });
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
      const r = new (require("events").EventEmitter)();
      r.end = () => queueMicrotask(() => r.emit("error", new Error("offline")));
      r.destroy = jest.fn();
      return r;
    },
  };
  const context = {
    module: { exports: {} },
    __dirname: servicesDir,
    console: { log: jest.fn(), error: jest.fn(), warn: jest.fn() },
    process: { platform: "linux", arch: "x64", env: { PATH: process.env.PATH } },
    setTimeout, clearTimeout, queueMicrotask, URL,
    require: (n) => {
      if (n === "fs") return fakeFs;
      if (n === "path") return path.posix;
      if (n === "os") return { ...require("os"), homedir: () => "/fixture" };
      if (n === "https") return offline;
      if (n === "./process-tree.cjs") return require(path.join(servicesDir, "process-tree.cjs"));
      if (n === "child_process") {
        return { spawn, execSync: jest.fn(), spawnSync: jest.fn(() => ({ status: 0, stdout: "GAIA version 0.25.0" })) };
      }
      return require(n);
    },
  };
  vm.runInNewContext(fs.readFileSync(installerPath, "utf8"), context, { filename: installerPath });
  const state = () => JSON.parse(files.get("/fixture/.gaia/electron-install-state.json") || "null");
  return { installer: context.module.exports, spawn, pids, state };
}

afterAll(() => {
  // Never leave a fixture behind, whatever a failed assertion skipped.
  for (const p of spawned) {
    try {
      if (typeof p === "number") process.kill(p, "SIGKILL");
      else p.kill("SIGKILL");
    } catch {
      // Already gone — the expected case.
    }
  }
});

describe("quitting during the backend install", () => {
  test("stops the running step and everything it started", async () => {
    const { installer, spawn, pids } = load();
    const quit = new AbortController();
    const install = installer.ensureBackend({ isPackaged: false, signal: quit.signal });
    const outcome = install.then(() => "installed", (err) => err);

    expect(await waitFor(() => pids.length >= 2)).toBe(true);
    expect(spawn).toHaveBeenCalledWith(expect.anything(), ["init", "--agent-only"], expect.anything());
    quit.abort();

    const err = await outcome;
    expect(err).toBeInstanceOf(installer.InstallCancelled);
    expect(await waitFor(() => pids.every((p) => !pidAlive(p)), 5000)).toBe(true);
  });

  test("is not recorded as failed, so the next launch runs it again", async () => {
    const { installer, pids, state } = load();
    const quit = new AbortController();
    const install = installer.ensureBackend({ isPackaged: false, signal: quit.signal }).catch((err) => err);
    expect(await waitFor(() => pids.length >= 2)).toBe(true);
    quit.abort();
    await install;

    expect(state()).toMatchObject({ state: "installing", stage: "agent-refresh" });
    expect(installer.isBackendCurrent()).toBe(false);
  });

  test("a step after the quit is never started", async () => {
    const { installer, spawn } = load();
    const quit = new AbortController();
    quit.abort();

    await expect(installer.ensureBackend({ isPackaged: false, signal: quit.signal })).rejects.toBeInstanceOf(
      installer.InstallCancelled
    );
    expect(spawn).not.toHaveBeenCalled();
  });
});
