// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

// An upgrade that hits a launcher another program holds open (#6570): only the
// locked launcher is moved aside, and the venv is never left without one.

const fs = require("fs");
const os = require("os");
const path = require("path");
const vm = require("vm");
const { EventEmitter } = require("events");

const installerPath = path.resolve(__dirname, "../../src/gaia/apps/webui/services/backend-installer.cjs");

const WIN_VENV = ["python.exe", "pythonw.exe", "gaia.exe", "gaia-mcp.exe", "uvicorn.exe"];

let home;

beforeEach(() => {
  home = fs.mkdtempSync(path.join(os.tmpdir(), "gaia-6570-"));
});

afterEach(() => {
  fs.rmSync(home, { recursive: true, force: true });
});

const scriptsDir = (platform) => path.join(home, ".gaia", "venv", platform === "win32" ? "Scripts" : "bin");

function seedVenv(platform, names) {
  const dir = scriptsDir(platform);
  fs.mkdirSync(dir, { recursive: true });
  for (const name of names) fs.writeFileSync(path.join(dir, name), `old ${name}`);
  return dir;
}

/** uv's error for a launcher it could not replace, spelled the way uv spells it. */
function lockOutput(name) {
  const via = [home, ".gaia", "venv", "Lib", "site-packages", "..", "..", "Scripts", name].join(path.sep);
  return (
    `error: failed to remove file \`${via}\`: ` +
    "The process cannot access the file because it is being used by another process. (os error 32)\n"
  );
}

/**
 * pipResults: one per `uv pip install`: { code, stderr, writes: [names uv
 * writes into Scripts] }. Other uv calls succeed.
 */
function loadInstaller(platform, pipResults, fsOverrides = {}) {
  const queue = [...pipResults];
  const spawn = jest.fn((cmd, args) => {
    const child = new EventEmitter();
    child.stdout = new EventEmitter();
    child.stderr = new EventEmitter();
    const result = args[0] === "pip" ? queue.shift() || { code: 0 } : { code: 0 };
    queueMicrotask(() => {
      for (const name of result.writes || []) fs.writeFileSync(path.join(scriptsDir(platform), name), `new ${name}`);
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
const read = (dir, name) => fs.readFileSync(path.join(dir, name), "utf8");
const asides = (dir) => fs.readdirSync(dir).filter((n) => /\.old-\d+$/.test(n)).sort();

describe("Windows: a locked launcher", () => {
  test("only the locked launcher is moved aside, and the install retried exactly once", async () => {
    const dir = seedVenv("win32", WIN_VENV);
    const { installer, pipCalls } = loadInstaller("win32", [
      { code: 2, stderr: lockOutput("gaia.exe") },
      { code: 0, writes: ["gaia.exe"] },
    ]);

    await install(installer);

    expect(pipCalls()).toHaveLength(2);
    expect(asides(dir)).toEqual([expect.stringMatching(/^gaia\.exe\.old-\d+$/)]);
    for (const name of ["python.exe", "pythonw.exe", "gaia-mcp.exe", "uvicorn.exe"]) {
      expect(read(dir, name)).toBe(`old ${name}`);
    }
  });

  test("a retry that reinstalls the launcher completes the install", async () => {
    const dir = seedVenv("win32", WIN_VENV);
    const { installer, logged } = loadInstaller("win32", [
      { code: 2, stderr: lockOutput("gaia.exe") },
      { code: 0, writes: ["gaia.exe"] },
    ]);

    await install(installer);

    expect(read(dir, "gaia.exe")).toBe("new gaia.exe");
    expect(logged()).toMatch(/Moved locked .*gaia\.exe aside/);
    expect(installer.getState().state).toBe(installer.STATES.READY);
  });

  test("a launcher the retry did not reinstall is put back", async () => {
    const dir = seedVenv("win32", WIN_VENV);
    const { installer } = loadInstaller("win32", [
      { code: 2, stderr: lockOutput("gaia.exe") },
      { code: 0 },
    ]);

    await install(installer);

    expect(read(dir, "gaia.exe")).toBe("old gaia.exe");
    expect(asides(dir)).toEqual([]);
  });

  test("a retry that fails puts the moved launcher back before reporting", async () => {
    const dir = seedVenv("win32", WIN_VENV);
    const { installer, pipCalls, logged } = loadInstaller("win32", [
      { code: 2, stderr: lockOutput("gaia.exe") },
      { code: 2, stderr: lockOutput("gaia-mcp.exe") },
    ]);

    const err = await install(installer).catch((e) => e);

    expect(pipCalls()).toHaveLength(2);
    expect(read(dir, "gaia.exe")).toBe("old gaia.exe");
    expect(asides(dir)).toEqual([]);
    expect(err.name).toBe("InstallError");
    expect(err.reason).toBe(installer.FAILURE_REASONS.LOCKED);
    expect(err.stage).toBe(installer.STAGES.INSTALL_PACKAGE);
    expect(err.message).toBe(
      "GAIA couldn't replace its own files because another program has them open (gaia-mcp.exe)."
    );
    expect(err.lockedFiles).toEqual([path.join(dir, "gaia-mcp.exe")]);
    expect(err.suggestion).toMatch(/restart your PC/);
    expect(logged()).toContain("(os error 32)");
  });

  test("a running launcher that can't be renamed is left alone and not retried", async () => {
    const dir = seedVenv("win32", WIN_VENV);
    const busy = Object.assign(new Error("EBUSY: resource busy or locked, rename"), { code: "EBUSY" });
    const { installer, pipCalls } = loadInstaller("win32", [{ code: 2, stderr: lockOutput("gaia.exe") }], {
      renameSync: () => {
        throw busy;
      },
    });

    const err = await install(installer).catch((e) => e);

    expect(pipCalls()).toHaveLength(1);
    expect(fs.readdirSync(dir).sort()).toEqual([...WIN_VENV].sort());
    expect(err.reason).toBe(installer.FAILURE_REASONS.LOCKED);
    expect(err.lockedFiles).toEqual([path.join(dir, "gaia.exe")]);
  });

  test("a locked file outside the venv's Scripts folder is never moved", async () => {
    const dir = seedVenv("win32", WIN_VENV);
    const elsewhere = path.join(home, "other", "gaia.exe");
    const output =
      `error: failed to remove file \`${elsewhere}\`: being used by another process. (os error 32)\n`;
    const { installer, pipCalls } = loadInstaller("win32", [{ code: 2, stderr: output }]);

    await install(installer).catch(() => {});

    expect(pipCalls()).toHaveLength(1);
    expect(asides(dir)).toEqual([]);
  });
});

describe("Windows: what an earlier upgrade moved aside", () => {
  test("is deleted once a fresh launcher sits at the original name", async () => {
    const dir = seedVenv("win32", [...WIN_VENV, "gaia.exe.old-1700000000000"]);
    const { installer } = loadInstaller("win32", [{ code: 0 }]);

    await install(installer);

    expect(asides(dir)).toEqual([]);
  });

  test("is put back, newest first, when the original is missing", async () => {
    const names = WIN_VENV.filter((n) => n !== "uvicorn.exe");
    const dir = seedVenv("win32", [...names, "uvicorn.exe.old-100", "uvicorn.exe.old-200"]);
    fs.writeFileSync(path.join(dir, "uvicorn.exe.old-200"), "newest uvicorn.exe");
    const { installer, logged } = loadInstaller("win32", [{ code: 0 }]);

    await install(installer);

    expect(read(dir, "uvicorn.exe")).toBe("newest uvicorn.exe");
    expect(asides(dir)).toEqual([]);
    expect(logged()).toMatch(/Put uvicorn\.exe back/);
  });

  test("one still in use is left for a later install", async () => {
    const dir = seedVenv("win32", [...WIN_VENV, "gaia.exe.old-1", "gaia-mcp.exe.old-1"]);
    const inUse = Object.assign(new Error("EBUSY: resource busy or locked"), { code: "EBUSY" });
    const { installer, logged } = loadInstaller("win32", [{ code: 0 }], {
      unlinkSync: (file) => {
        if (String(file).endsWith("gaia.exe.old-1")) throw inUse;
        fs.unlinkSync(file);
      },
    });

    await install(installer);

    expect(asides(dir)).toEqual(["gaia.exe.old-1"]);
    expect(logged()).toMatch(/gaia\.exe\.old-1.*still in use/);
  });

  test("any other error deleting one fails the install", async () => {
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
    const { installer, pipCalls } = loadInstaller("linux", [{ code: 2, stderr: lockOutput("gaia.exe") }]);

    const err = await install(installer).catch((e) => e);

    expect(pipCalls()).toHaveLength(1);
    expect(err.reason).toBe(installer.FAILURE_REASONS.LOCKED);
    expect(fs.readdirSync(dir).sort()).toEqual(["gaia", "gaia.exe.old-1", "python"]);
  });
});
