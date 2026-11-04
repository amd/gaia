// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

// An upgrade stops the GAIA daemon the old venv runs before touching that
// venv (#6572): a daemon holding its files would fail the install on Windows.

const fs = require("fs");
const os = require("os");
const path = require("path");
const vm = require("vm");
const { EventEmitter } = require("events");

const installerPath = path.resolve(__dirname, "../../src/gaia/apps/webui/services/backend-installer.cjs");

// Mirrors DAEMON_STOP_LEMONADE_EXIT in gaia/cli.py; pinned by a test below.
const LEMONADE_EXIT = 3;

let home;

beforeEach(() => {
  home = fs.mkdtempSync(path.join(os.tmpdir(), "gaia-6572-"));
});

afterEach(() => {
  fs.rmSync(home, { recursive: true, force: true });
});

const venvBin = (platform) => path.join(home, ".gaia", "venv", platform === "win32" ? "Scripts" : "bin");
const gaiaBin = (platform) => path.join(venvBin(platform), platform === "win32" ? "gaia.exe" : "gaia");

function seedVenv(platform, { python = true } = {}) {
  const dir = venvBin(platform);
  fs.mkdirSync(dir, { recursive: true });
  fs.writeFileSync(gaiaBin(platform), "launcher");
  if (python) fs.writeFileSync(path.join(dir, platform === "win32" ? "python.exe" : "python"), "python");
}

/** Write ~/.gaia/host/instance.json as a running daemon does. */
function seedInstance(content) {
  const dir = path.join(home, ".gaia", "host");
  fs.mkdirSync(dir, { recursive: true });
  const body = typeof content === "number" ? JSON.stringify({ pid: content, port: 1, token: "t" }) : content;
  fs.writeFileSync(path.join(dir, "instance.json"), body);
}

const isStop = ([cmd, argv]) => argv[0] === "daemon" && argv[1] === "stop";

const processError = (code) => Object.assign(new Error(code), { code });

/**
 * stop: what `gaia daemon stop` does — { code, stderr } or { hang: true }.
 * Every other command succeeds. livePids are running processes; epermPids
 * are running as another account.
 */
function loadInstaller(platform, { stop = { code: 0 }, livePids = [], epermPids = [] } = {}) {
  const children = [];
  const spawn = jest.fn((cmd, argv) => {
    const child = new EventEmitter();
    child.stdout = new EventEmitter();
    child.stderr = new EventEmitter();
    child.pid = 9000 + children.length;
    child.kill = jest.fn();
    children.push(child);
    const result = isStop([cmd, argv]) ? stop : { code: 0 };
    if (!result.hang) {
      queueMicrotask(() => {
        if (result.stderr) child.stderr.emit("data", Buffer.from(result.stderr));
        child.emit("exit", result.code);
      });
    }
    return child;
  });
  const spawnSync = jest.fn(() => ({ status: 0, stdout: "0.25.4" }));
  const kill = jest.fn((pid, signal) => {
    if (signal !== 0) throw new Error(`the installer may only probe a pid, not send it ${signal}`);
    if (livePids.includes(pid)) return true;
    if (epermPids.includes(pid)) throw processError("EPERM");
    throw processError("ESRCH");
  });
  const context = {
    module: { exports: {} },
    __dirname: path.dirname(installerPath),
    console: { log: jest.fn(), error: jest.fn(), warn: jest.fn() },
    process: { platform, arch: "x64", env: { GAIA_TEST: "1" }, pid: 1, kill },
    Buffer,
    require: (name) => {
      if (name === "os") return { ...os, homedir: () => home };
      if (name === "child_process") return { spawn, execSync: jest.fn(), spawnSync };
      return require(name);
    },
  };
  vm.runInNewContext(fs.readFileSync(installerPath, "utf8"), context, { filename: installerPath });
  const logged = () =>
    [...context.console.log.mock.calls, ...context.console.error.mock.calls].map((c) => c.join(" ")).join("\n");
  return { installer: context.module.exports, spawn, spawnSync, kill, children, logged };
}

const install = (installer) => installer.installBackend({ version: "0.25.4", isPackaged: false });
const ranPipInstall = (spawn) => spawn.mock.calls.some(([cmd, argv]) => cmd === "uv" && argv[0] === "pip");

describe.each(["win32", "linux"])("an upgrade on %s", (platform) => {
  test("stops the daemon with the existing venv's gaia before any uv command", async () => {
    seedVenv(platform);
    const { installer, spawn } = loadInstaller(platform);

    await install(installer);

    const calls = spawn.mock.calls;
    const stopAt = calls.findIndex(isStop);
    expect(stopAt).toBeGreaterThanOrEqual(0);
    expect(calls[stopAt][0]).toBe(gaiaBin(platform));
    expect(calls.slice(0, stopAt).filter(([cmd]) => cmd === "uv")).toEqual([]);
    expect(calls.findIndex(([cmd, argv]) => cmd === "uv" && argv[0] === "pip")).toBeGreaterThan(stopAt);
    // gaia prints emoji a Windows pipe can't encode.
    expect(calls[stopAt][2].env).toEqual({ GAIA_TEST: "1", PYTHONIOENCODING: "utf-8" });
    expect(installer.getState().state).toBe(installer.STATES.READY);
  });

  test("a stop that leaves the daemon running ends the install, naming it, before uv runs", async () => {
    seedVenv(platform);
    seedInstance(4242);
    const { installer, spawn, logged } = loadInstaller(platform, {
      stop: { code: 1, stderr: "❌ GAIA daemon pid 4242 is still running after it was terminated." },
      livePids: [4242],
    });

    const err = await install(installer).catch((e) => e);

    expect(err.name).toBe("InstallError");
    expect(err.reason).toBe(installer.FAILURE_REASONS.DAEMON);
    expect(err.stage).toBe(installer.STAGES.CREATE_VENV);
    expect(err.message).toBe(
      "GAIA's background service (the GAIA daemon, pid 4242) could not be stopped (gaia daemon stop exit 1), so its files can't be updated."
    );
    expect(err.suggestion).toMatch(/no window to close/);
    expect(spawn.mock.calls.filter(([cmd]) => cmd === "uv")).toEqual([]);
    expect(logged()).toContain("pid 4242 is still running");
    expect(logged()).toContain(`${gaiaBin(platform)} daemon stop`);
  });

  test("a daemon running as another account still counts as running", async () => {
    seedVenv(platform);
    seedInstance(4242);
    const { installer } = loadInstaller(platform, { stop: { code: 1 }, epermPids: [4242] });

    const err = await install(installer).catch((e) => e);

    expect(err.reason).toBe(installer.FAILURE_REASONS.DAEMON);
  });

  test("a broken venv whose stop fails, with no daemon running, is repaired by the install", async () => {
    seedVenv(platform);
    const { installer, spawn, logged } = loadInstaller(platform, {
      stop: { code: 1, stderr: "ModuleNotFoundError: No module named 'gaia'" },
    });

    await install(installer);

    expect(installer.getState().state).toBe(installer.STATES.READY);
    expect(ranPipInstall(spawn)).toBe(true);
    expect(logged()).toContain("exited 1, but no GAIA daemon is running");
  });

  test("a failed stop whose instance.json names a dead pid lets the install go on", async () => {
    seedVenv(platform);
    seedInstance(4242);
    const { installer, kill } = loadInstaller(platform, { stop: { code: 1 } });

    await install(installer);

    expect(installer.getState().state).toBe(installer.STATES.READY);
    expect(kill).toHaveBeenCalledWith(4242, 0);
  });

  test.each([
    ["corrupt", "{not json"],
    ["pid-less", JSON.stringify({ port: 1 })],
    ["non-numeric pid", JSON.stringify({ pid: "abc" })],
    ["zero pid", JSON.stringify({ pid: 0 })],
  ])("a %s instance.json is no running daemon", async (_label, body) => {
    seedVenv(platform);
    seedInstance(body);
    const { installer, kill } = loadInstaller(platform, { stop: { code: 1 } });

    await install(installer);

    expect(installer.getState().state).toBe(installer.STATES.READY);
    expect(kill).not.toHaveBeenCalled();
  });

  test("a Lemonade Server that won't stop is named, and doesn't block the install", async () => {
    seedVenv(platform);
    const { installer, spawn, logged } = loadInstaller(platform, {
      stop: { code: LEMONADE_EXIT, stderr: "❌ GAIA's Lemonade Server did not stop: pid 9 is not it" },
    });

    await install(installer);

    expect(installer.getState().state).toBe(installer.STATES.READY);
    expect(ranPipInstall(spawn)).toBe(true);
    expect(logged()).toContain("The GAIA daemon stopped, but GAIA's Lemonade Server did not");
    expect(logged()).not.toMatch(/Restart your PC/);
  });

  test("with the daemon still running too, a Lemonade exit fails on the daemon", async () => {
    seedVenv(platform);
    seedInstance(4242);
    const { installer } = loadInstaller(platform, { stop: { code: LEMONADE_EXIT }, livePids: [4242] });

    const err = await install(installer).catch((e) => e);

    expect(err.reason).toBe(installer.FAILURE_REASONS.DAEMON);
  });

  test("a GAIA from before the daemon has nothing to stop, and the install goes on", async () => {
    seedVenv(platform);
    const { installer, logged } = loadInstaller(platform, {
      stop: { code: 2, stderr: "gaia: error: argument action: invalid choice: 'daemon' (choose from 'chat', 'llm')" },
    });

    await install(installer);

    expect(installer.getState().state).toBe(installer.STATES.READY);
    expect(logged()).toMatch(/predates the daemon/);
  });

  test("any other exit 2 with the daemon still running fails the install", async () => {
    seedVenv(platform);
    seedInstance(4242);
    const { installer } = loadInstaller(platform, { stop: { code: 2, stderr: "usage error" }, livePids: [4242] });

    const err = await install(installer).catch((e) => e);

    expect(err.reason).toBe(installer.FAILURE_REASONS.DAEMON);
  });

  test("a fresh install has no daemon to stop", async () => {
    const { installer, spawn } = loadInstaller(platform);

    // The mocked uv writes no venv, so the install fails later; only the stop matters here.
    await install(installer).catch(() => {});

    expect(spawn.mock.calls.filter(isStop)).toEqual([]);
    expect(spawn.mock.calls.some(([cmd]) => cmd === "uv")).toBe(true);
  });

  test("a venv with no Python can't be running a daemon, so none is stopped", async () => {
    seedVenv(platform, { python: false });
    const { installer, spawn } = loadInstaller(platform);

    await install(installer).catch(() => {});

    expect(spawn.mock.calls.filter(isStop)).toEqual([]);
  });
});

describe("the stop's exit codes", () => {
  test("the Lemonade exit code matches the CLI's", () => {
    const { installer } = loadInstaller("win32");
    const cli = fs.readFileSync(path.resolve(__dirname, "../../src/gaia/cli.py"), "utf8");

    expect(installer.DAEMON_STOP_LEMONADE_EXIT).toBe(LEMONADE_EXIT);
    expect(cli).toMatch(new RegExp(`^DAEMON_STOP_LEMONADE_EXIT = ${LEMONADE_EXIT}$`, "m"));
  });
});

describe("a stop that hangs", () => {
  test("is time-boxed to two minutes by default", () => {
    const { installer } = loadInstaller("win32");
    expect(installer.DAEMON_STOP_TIMEOUT_MS).toBe(120 * 1000);
  });

  test("Windows: ends the whole stop process tree and fails, naming the daemon", async () => {
    seedVenv("win32");
    seedInstance(4242);
    const { installer, spawnSync, children } = loadInstaller("win32", { stop: { hang: true }, livePids: [4242] });

    const err = await installer.stopRunningDaemon({ timeoutMs: 20 }).catch((e) => e);

    expect(err.reason).toBe(installer.FAILURE_REASONS.DAEMON);
    expect(err.message).toMatch(/^GAIA's background service \(the GAIA daemon, pid 4242\) did not stop within \d+ seconds/);
    const pid = String(children[0].pid);
    expect(spawnSync).toHaveBeenCalledWith("taskkill", ["/PID", pid, "/T", "/F"], expect.any(Object));
  });

  test("Linux: kills the stop process and fails", async () => {
    seedVenv("linux");
    seedInstance(4242);
    const { installer, children } = loadInstaller("linux", { stop: { hang: true }, livePids: [4242] });

    const err = await installer.stopRunningDaemon({ timeoutMs: 20 }).catch((e) => e);

    expect(err.reason).toBe(installer.FAILURE_REASONS.DAEMON);
    expect(children[0].kill).toHaveBeenCalledWith("SIGKILL");
  });

  test("with no daemon left running, a timed-out stop doesn't block the install", async () => {
    seedVenv("linux");
    const { installer, logged } = loadInstaller("linux", { stop: { hang: true } });

    await installer.stopRunningDaemon({ timeoutMs: 20 });

    expect(logged()).toContain("timed out, but no GAIA daemon is running");
  });

  test("a stop that exits in time leaves no timer running", async () => {
    seedVenv("linux");
    const { installer, children } = loadInstaller("linux");

    await installer.stopRunningDaemon({ timeoutMs: 20 });
    await new Promise((r) => setTimeout(r, 60));

    expect(children[0].kill).not.toHaveBeenCalled();
  });
});
