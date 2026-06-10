// Tests for backend-orphan.cjs — the orphaned-backend identity probe that
// guards the Windows upgrade lock fix (issue #1388).

const {
  isGaiaBackendProcess,
  commandLooksLikeBackend,
} = require("../../src/gaia/apps/webui/services/backend-orphan.cjs");

describe("commandLooksLikeBackend", () => {
  test("matches a real backend invocation", () => {
    expect(
      commandLooksLikeBackend("gaia chat --ui --ui-port 4321")
    ).toBe(true);
  });

  test("matches gaia + --ui-port even without the chat token", () => {
    expect(commandLooksLikeBackend("python gaia --ui-port 5000")).toBe(true);
  });

  test("rejects unrelated processes", () => {
    expect(commandLooksLikeBackend("python -m jupyter lab")).toBe(false);
    expect(commandLooksLikeBackend("gaia init --yes")).toBe(false);
    expect(commandLooksLikeBackend("")).toBe(false);
    expect(commandLooksLikeBackend(undefined)).toBe(false);
  });
});

describe("isGaiaBackendProcess (win32)", () => {
  const makeSpawnSync = (stdout) =>
    jest.fn(() => ({ stdout, status: 0 }));

  test("identifies a backend via the PowerShell CommandLine probe", () => {
    const spawnSync = makeSpawnSync(
      "C:\\Users\\me\\.gaia\\venv\\Scripts\\gaia.exe chat --ui --ui-port 4321\r\n"
    );
    const result = isGaiaBackendProcess(1234, {
      platform: "win32",
      spawnSync,
    });
    expect(result).toBe(true);
    // Conservative probe: queries the specific PID, not a broad scan.
    const [cmd, args] = spawnSync.mock.calls[0];
    expect(cmd).toBe("powershell.exe");
    expect(args.join(" ")).toContain("ProcessId=1234");
  });

  test("does not match an unrelated win32 process", () => {
    const spawnSync = makeSpawnSync("C:\\Windows\\System32\\notepad.exe\r\n");
    expect(
      isGaiaBackendProcess(1234, { platform: "win32", spawnSync })
    ).toBe(false);
  });

  test("returns false when the PID is gone (empty CommandLine)", () => {
    const spawnSync = makeSpawnSync("");
    expect(
      isGaiaBackendProcess(9999, { platform: "win32", spawnSync })
    ).toBe(false);
  });

  test("returns false (never throws) when the probe blows up", () => {
    const spawnSync = jest.fn(() => {
      throw new Error("powershell not found");
    });
    expect(
      isGaiaBackendProcess(1234, { platform: "win32", spawnSync })
    ).toBe(false);
  });
});

describe("isGaiaBackendProcess (linux)", () => {
  test("matches via /proc/<pid>/cmdline", () => {
    const fs = {
      existsSync: jest.fn(() => true),
      readFileSync: jest.fn(() => "gaia\0chat\0--ui\0--ui-port\04321"),
    };
    expect(
      isGaiaBackendProcess(1234, { platform: "linux", fs })
    ).toBe(true);
  });

  test("returns false when /proc entry is missing", () => {
    const fs = { existsSync: jest.fn(() => false), readFileSync: jest.fn() };
    expect(
      isGaiaBackendProcess(1234, { platform: "linux", fs })
    ).toBe(false);
  });
});

describe("isGaiaBackendProcess (darwin)", () => {
  test("matches via ps command output", () => {
    const spawnSync = jest.fn(() => ({
      stdout: "gaia chat --ui --ui-port 4321\n",
    }));
    expect(
      isGaiaBackendProcess(1234, { platform: "darwin", spawnSync })
    ).toBe(true);
  });
});
