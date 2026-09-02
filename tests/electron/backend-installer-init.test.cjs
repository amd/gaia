// Regression coverage for failed model initialization (issue #3206).

const fs = require("fs");
const os = require("os");
const path = require("path");
const { EventEmitter } = require("events");

jest.mock("child_process", () => {
  const actual = jest.requireActual("child_process");
  return {
    ...actual,
    execSync: jest.fn(() => Buffer.from("uv 0.5.14\n")),
    spawnSync: jest.fn(() => ({
      status: 0,
      stdout: Buffer.from("0.0.0\n"),
      stderr: Buffer.alloc(0),
    })),
    spawn: jest.fn((command, args) => {
      const child = new EventEmitter();
      child.stdout = new EventEmitter();
      child.stderr = new EventEmitter();
      const isInit = args[0] === "init";
      process.nextTick(() => child.emit("exit", isInit ? 17 : 0));
      return child;
    }),
  };
});

const childProcess = require("child_process");
const testHome = fs.mkdtempSync(path.join(os.tmpdir(), "gaia-init-test-"));
const homeSpy = jest.spyOn(os, "homedir").mockReturnValue(testHome);
const installer = require("../../src/gaia/apps/webui/services/backend-installer.cjs");

const fakePython = path.join(testHome, ".gaia", "venv", "Scripts", "python.exe");
const fakeGaia = path.join(testHome, ".gaia", "venv", "Scripts", "gaia.exe");

beforeAll(() => {
  fs.mkdirSync(path.dirname(fakePython), { recursive: true });
  fs.writeFileSync(fakePython, "");
  fs.writeFileSync(fakeGaia, "");
});

beforeEach(() => {
  installer.clearState();
  childProcess.spawn.mockClear();
  childProcess.spawnSync.mockClear();
  childProcess.execSync.mockClear();
});

afterAll(() => {
  homeSpy.mockRestore();
  fs.rmSync(testHome, { recursive: true, force: true });
});

describe("installBackend gaia init failures", () => {
  test("fails the install and records a retryable gaia-init error", async () => {
    const onProgress = jest.fn();

    await expect(
      installer.installBackend({
        isPackaged: false,
        version: "0.0.0",
        onProgress,
      })
    ).rejects.toMatchObject({
      name: "InstallError",
      stage: installer.STAGES.GAIA_INIT,
      code: 17,
      suggestion: expect.stringContaining("Retry"),
    });

    expect(installer.getState()).toMatchObject({
      state: installer.STATES.INSTALLING,
      stage: installer.STAGES.GAIA_INIT,
    });
    expect(onProgress).not.toHaveBeenCalledWith(
      installer.STAGES.GAIA_INIT,
      100,
      "Lemonade Server setup complete"
    );
    expect(childProcess.spawn).toHaveBeenCalledWith(
      fakeGaia,
      ["init", "--profile", "minimal", "--yes"],
      expect.any(Object)
    );
  });

  test("still supports explicitly skipping gaia init", async () => {
    await expect(
      installer.installBackend({
        isPackaged: false,
        version: "0.0.0",
        skipGaiaInit: true,
      })
    ).resolves.toBeUndefined();

    expect(childProcess.spawn).not.toHaveBeenCalledWith(
      fakeGaia,
      ["init", "--profile", "minimal", "--yes"],
      expect.any(Object)
    );
    expect(installer.getState()).toMatchObject({
      state: installer.STATES.READY,
      version: "0.0.0",
    });
  });
});
