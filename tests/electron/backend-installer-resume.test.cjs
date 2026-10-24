// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

// "Installed" must mean `gaia init` finished, not that the venv exists: an
// install interrupted during setup used to report "already installed" on the
// next launch and never set up Lemonade or the models.

const fs = require("fs");
const path = require("path");
const vm = require("vm");
const { EventEmitter } = require("events");

const installerPath = path.resolve(
  __dirname,
  "../../src/gaia/apps/webui/services/backend-installer.cjs"
);

// `initRun` is what `gaia init` prints and exits with; every other command succeeds.
function loadInstaller(previousState, initRun = { code: 0 }) {
  const files = new Map();
  if (previousState) {
    files.set("/fixture/.gaia/electron-install-state.json", JSON.stringify(previousState));
  }
  const spawn = jest.fn((cmd, argv) => {
    const child = new EventEmitter();
    child.stdout = new EventEmitter();
    child.stderr = new EventEmitter();
    const run = argv[0] === "init" ? initRun : { code: 0 };
    queueMicrotask(() => {
      if (run.stdout) child.stdout.emit("data", run.stdout);
      child.emit("exit", run.code);
    });
    return child;
  });
  const fakeFs = {
    ...fs,
    existsSync: (file) => String(file).includes("/venv") || files.has(String(file)),
    readFileSync: (file, enc) =>
      files.has(String(file)) ? files.get(String(file)) : fs.readFileSync(file, enc),
    writeFileSync: (file, data) => files.set(String(file), String(data)),
    mkdirSync: jest.fn(),
    statfsSync: () => ({ bavail: 1e9, bsize: 4096 }),
    createWriteStream: () => ({ write: jest.fn(), end: jest.fn(), on: jest.fn() }),
  };
  const offline = {
    request: () => {
      const req = new EventEmitter();
      req.end = () => queueMicrotask(() => req.emit("error", new Error("offline")));
      req.destroy = jest.fn();
      return req;
    },
  };
  const context = {
    module: { exports: {} },
    __dirname: path.dirname(installerPath),
    console: { log: jest.fn(), error: jest.fn(), warn: jest.fn() },
    process: { platform: "linux", arch: "x64", env: {} },
    setTimeout,
    clearTimeout,
    queueMicrotask,
    URL,
    require: (name) => {
      if (name === "fs") return fakeFs;
      if (name === "path") return path.posix;
      if (name === "os") return { ...require("os"), homedir: () => "/fixture" };
      if (name === "https") return offline;
      if (name === "child_process") {
        return {
          spawn,
          execSync: jest.fn(),
          spawnSync: jest.fn(() => ({ status: 0, stdout: "GAIA version 0.25.0" })),
        };
      }
      return require(name);
    },
  };
  vm.runInNewContext(fs.readFileSync(installerPath, "utf8"), context, {
    filename: installerPath,
  });
  const state = () =>
    JSON.parse(files.get("/fixture/.gaia/electron-install-state.json") || "null");
  return { installer: context.module.exports, spawn, state };
}

const initRuns = (spawn) => spawn.mock.calls.filter(([, argv]) => argv[0] === "init");
const pipRuns = (spawn) => spawn.mock.calls.filter(([, argv]) => argv[0] === "pip");

describe("an install whose setup never finished", () => {
  test("interrupted during gaia init: the next launch runs gaia init, not 'already installed'", async () => {
    const { installer, spawn, state } = loadInstaller({
      state: "installing",
      stage: "gaia-init",
      version: "0.25.0",
    });

    await installer.ensureBackend({ version: "0.25.0", isPackaged: false });

    expect(initRuns(spawn)).toHaveLength(1);
    expect(initRuns(spawn)[0][1]).toEqual(
      expect.arrayContaining(["--profile", "gaia", "--yes"])
    );
    // The package was already in place: setup resumes, nothing reinstalls.
    expect(pipRuns(spawn)).toHaveLength(0);
    expect(state()).toMatchObject({ state: "ready", init: "done" });
  });

  test("a setup that failed last time runs again", async () => {
    const { installer, spawn, state } = loadInstaller({
      state: "ready",
      version: "0.25.0",
      init: "failed",
    });

    await installer.ensureBackend({ version: "0.25.0", isPackaged: false });

    expect(initRuns(spawn)).toHaveLength(1);
    expect(state()).toMatchObject({ state: "ready", init: "done" });
  });

  test("interrupted while installing the package: it reinstalls, then sets up", async () => {
    const { installer, spawn } = loadInstaller({
      state: "installing",
      stage: "install-package",
      version: "0.25.0",
    });

    await installer.ensureBackend({ version: "0.25.0", isPackaged: false });

    expect(pipRuns(spawn).length).toBeGreaterThan(0);
    expect(initRuns(spawn)).toHaveLength(1);
  });
});

describe("a setup that fails", () => {
  const memoryError =
    "Gemma-4-E4B-it-GGUF - Not enough free memory to load Gemma-4-E4B-it-GGUF with a " +
    "32,768-token window: needs about 7.8 GB and only 6.0 GB is free. See https://amd-gaia.ai/docs";

  test("records what gaia init said, so the app can show it before the first message", async () => {
    const { installer, spawn, state } = loadInstaller(
      { state: "installing", stage: "gaia-init", version: "0.25.0" },
      {
        code: 1,
        stdout:
          "Step 5/5: Verifying setup...\n" +
          `   ❌ ${memoryError}\n` +
          "   ⚠️  Models verified: 1/2 passed\n" +
          "   Failed models may be corrupted. To fix:\n",
      }
    );

    await installer.ensureBackend({ version: "0.25.0", isPackaged: false });

    expect(state()).toMatchObject({ state: "ready", init: "failed", initError: memoryError });
    // Unwrapped, or the error line would be cut at 80 columns.
    expect(initRuns(spawn)[0][2].env.COLUMNS).toBe("1000");
  });

  test("without an error line, the reason is its last line, else its exit code", () => {
    const { installer } = loadInstaller(null);
    expect(
      installer.initFailureReason({ code: 1, stdout: "Step 1/5\nLemonade did not start\n", stderr: "" })
    ).toBe("Lemonade did not start");
    expect(
      installer.initFailureReason({
        code: 1,
        stdout: 'Lemonade did not start\n{"event": "done", "ok": false}\n',
        stderr: "",
      })
    ).toBe("Lemonade did not start");
    expect(installer.initFailureReason({ code: 2, stdout: "", stderr: "" })).toBe(
      "gaia init exited with code 2 and printed nothing."
    );
  });
});

describe("a finished install", () => {
  test("is ready without running anything", async () => {
    const { installer, spawn } = loadInstaller({
      state: "ready",
      version: "0.25.0",
      init: "done",
    });

    await installer.ensureBackend({ version: "0.25.0", isPackaged: false });

    expect(spawn).not.toHaveBeenCalled();
  });

  test("an install from before the state file existed is left alone", async () => {
    const { installer, spawn } = loadInstaller(null);

    await installer.ensureBackend({ version: "0.25.0", isPackaged: false });

    expect(spawn).not.toHaveBeenCalled();
  });
});
