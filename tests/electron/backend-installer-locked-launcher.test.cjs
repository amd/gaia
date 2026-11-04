// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

// An upgrade must get past a gaia.exe that another program holds open (#6570).

const fs = require("fs");
const os = require("os");
const path = require("path");
const vm = require("vm");
const { EventEmitter } = require("events");

const installerPath = path.resolve(__dirname, "../../src/gaia/apps/webui/services/backend-installer.cjs");

const LOCK_OUTPUT =
  "error: failed to remove file `C:\\Users\\x\\.gaia\\venv\\Lib\\site-packages\\../../Scripts/gaia.exe`: " +
  "The process cannot access the file because it is being used by another process. (os error 32)\n";

let home;

beforeEach(() => {
  home = fs.mkdtempSync(path.join(os.tmpdir(), "gaia-6570-"));
});

afterEach(() => {
  fs.rmSync(home, { recursive: true, force: true });
});

function venvDir(platform) {
  return path.join(home, ".gaia", "venv", platform === "win32" ? "Scripts" : "bin");
}

function seedVenv(platform, names) {
  const dir = venvDir(platform);
  fs.mkdirSync(dir, { recursive: true });
  for (const name of names) fs.writeFileSync(path.join(dir, name), name);
  return dir;
}

/** pipResults: one { code, stderr } per `uv pip install`; other uv calls succeed. */
function loadInstaller(platform, pipResults, fsOverrides = {}) {
  const queue = [...pipResults];
  const spawn = jest.fn((cmd, args) => {
    const child = new EventEmitter();
    child.stdout = new EventEmitter();
    child.stderr = new EventEmitter();
    const result = args[0] === "pip" ? queue.shift() || { code: 0 } : { code: 0 };
    queueMicrotask(() => {
      if (result.stderr) child.stderr.emit("data", Buffer.from(result.stderr));
      child.emit("exit", result.code);
    });
    return child;
  });
  const fakeFs = { ...fs, ...fsOverrides };
  const context = {
    module: { exports: {} },
    __dirname: path.dirname(installerPath),
    console: { log: jest.fn(), error: jest.fn(), warn: jest.fn() },
    process: { platform, arch: "x64", env: {}, pid: 1 },
    Buffer,
    require: (name) => {
      if (name === "fs") return fakeFs;
      if (name === "os") return { ...os, homedir: () => home };
      if (name === "child_process") return {
        spawn,
        execSync: jest.fn(),
        spawnSync: jest.fn(() => ({ status: 0, stdout: "0.25.4" })),
      };
      return require(name);
    },
  };
  vm.runInNewContext(fs.readFileSync(installerPath, "utf8"), context, { filename: installerPath });
  const installer = context.module.exports;
  const pipCalls = () => spawn.mock.calls.filter(([, argv]) => argv[0] === "pip");
  const logged = () =>
    [...context.console.log.mock.calls, ...context.console.error.mock.calls].map((c) => c.join(" ")).join("\n");
  return { installer, pipCalls, logged };
}

const install = (installer) => installer.installBackend({ version: "0.25.4", isPackaged: false });

describe("Windows: a locked launcher", () => {
  const WIN_VENV = ["python.exe", "pythonw.exe", "gaia.exe", "gaia-mcp.exe"];

  test("is moved aside and the install retried exactly once", async () => {
    seedVenv("win32", WIN_VENV);
    const { installer, pipCalls } = loadInstaller("win32", [
      { code: 2, stderr: LOCK_OUTPUT },
      { code: 2, stderr: LOCK_OUTPUT },
      { code: 0 },
    ]);

    await install(installer).catch(() => {});

    expect(pipCalls()).toHaveLength(2);
  });

  test("a retry that succeeds completes the install and keeps the interpreter in place", async () => {
    const dir = seedVenv("win32", WIN_VENV);
    const { installer, pipCalls, logged } = loadInstaller("win32", [{ code: 2, stderr: LOCK_OUTPUT }], {
      // uv writes fresh launchers on the retry.
      renameSync: (from, to) => {
        fs.renameSync(from, to);
        fs.writeFileSync(from, "new");
      },
    });

    await install(installer);

    expect(pipCalls()).toHaveLength(2);
    const names = fs.readdirSync(dir);
    expect(names).toEqual(expect.arrayContaining(["python.exe", "pythonw.exe", "gaia.exe", "gaia-mcp.exe"]));
    expect(names.filter((n) => /^gaia\.exe\.old-\d+$/.test(n))).toHaveLength(1);
    expect(names.filter((n) => /^gaia-mcp\.exe\.old-\d+$/.test(n))).toHaveLength(1);
    expect(names.some((n) => n.startsWith("python") && n.includes(".old-"))).toBe(false);
    expect(logged()).toMatch(/Moved .*gaia\.exe.* aside/);
    expect(installer.getState().state).toBe(installer.STATES.READY);
  });

  test("a second lock failure ends in a plain-language error, pip's line in the log", async () => {
    seedVenv("win32", WIN_VENV);
    const { installer, pipCalls, logged } = loadInstaller("win32", [
      { code: 2, stderr: LOCK_OUTPUT },
      { code: 2, stderr: LOCK_OUTPUT },
    ]);

    const err = await install(installer).catch((e) => e);

    expect(pipCalls()).toHaveLength(2);
    expect(err.name).toBe("InstallError");
    expect(err.reason).toBe(installer.FAILURE_REASONS.LOCKED);
    expect(err.stage).toBe(installer.STAGES.INSTALL_PACKAGE);
    expect(err.message).toBe(
      "GAIA couldn't replace its own files because another program has them open (gaia.exe)."
    );
    expect(err.message).not.toMatch(/pip|os error/i);
    expect(err.suggestion).toMatch(/restart your PC/);
    expect(logged()).toContain("(os error 32)");
  });

  test("files an earlier upgrade moved aside are deleted at the next install", async () => {
    const dir = seedVenv("win32", [...WIN_VENV, "gaia.exe.old-1700000000000", "gaia-mcp.exe.old-1700000000000"]);
    const { installer } = loadInstaller("win32", [{ code: 0 }]);

    await install(installer);

    expect(fs.readdirSync(dir).filter((n) => n.includes(".old-"))).toEqual([]);
  });

  test("a moved-aside file still in use is left for a later start", async () => {
    const dir = seedVenv("win32", [...WIN_VENV, "gaia.exe.old-1", "gaia-mcp.exe.old-1"]);
    const inUse = Object.assign(new Error("EBUSY: resource busy or locked"), { code: "EBUSY" });
    const { installer, logged } = loadInstaller("win32", [{ code: 0 }], {
      unlinkSync: (file) => {
        if (String(file).endsWith("gaia.exe.old-1")) throw inUse;
        fs.unlinkSync(file);
      },
    });

    await install(installer);

    expect(fs.readdirSync(dir).filter((n) => n.includes(".old-"))).toEqual(["gaia.exe.old-1"]);
    expect(logged()).toMatch(/gaia\.exe\.old-1.*still in use/);
  });

  test("any other error deleting a moved-aside file fails the install", async () => {
    seedVenv("win32", [...WIN_VENV, "gaia.exe.old-1"]);
    const broken = Object.assign(new Error("EIO: i/o error"), { code: "EIO" });
    const { installer, pipCalls } = loadInstaller("win32", [{ code: 0 }], {
      unlinkSync: () => {
        throw broken;
      },
    });

    await expect(install(installer)).rejects.toThrow(/EIO/);
    expect(pipCalls()).toHaveLength(0);
  });
});

describe("non-Windows", () => {
  test("a lock-shaped failure is not retried and nothing is renamed", async () => {
    const dir = seedVenv("linux", ["python", "gaia", "gaia.exe.old-1"]);
    const { installer, pipCalls } = loadInstaller("linux", [{ code: 2, stderr: LOCK_OUTPUT }]);

    const err = await install(installer).catch((e) => e);

    expect(pipCalls()).toHaveLength(1);
    expect(err.reason).toBe(installer.FAILURE_REASONS.LOCKED);
    expect(fs.readdirSync(dir).sort()).toEqual(["gaia", "gaia.exe.old-1", "python"]);
  });
});
