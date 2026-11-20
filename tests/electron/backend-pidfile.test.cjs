// Per-app backend records: two copies of the desktop app sharing one ~/.gaia
// must never stop each other's live backend (issue #7062), while a backend
// whose app died is still stopped before an upgrade (issue #1388).

const fs = require("fs");
const os = require("os");
const path = require("path");

const {
  cleanupOrphanedBackends,
  recordBackend,
  forgetBackend,
  backendRecordPath,
  PIDFILE_DIR,
  LEGACY_PIDFILE,
} = require("../../src/gaia/apps/webui/services/backend-orphan.cjs");

const quiet = { log: () => {}, warn: () => {} };

let gaiaDir;
beforeEach(() => {
  gaiaDir = fs.mkdtempSync(path.join(os.tmpdir(), "gaia-pidfile-"));
});
afterEach(() => {
  fs.rmSync(gaiaDir, { recursive: true, force: true });
});

/** Run cleanup as app `selfPid`, with `alive` the set of running app pids. */
function cleanupAs(selfPid, { alive = [], backends = null, killBackend = jest.fn(async () => {}) } = {}) {
  const isBackend = jest.fn((pid) => (backends ? backends.includes(pid) : true));
  return cleanupOrphanedBackends({
    gaiaDir,
    selfPid,
    killBackend,
    isBackend,
    isAlive: (pid) => alive.includes(pid),
    logger: quiet,
  }).then((result) => ({ ...result, killBackend, isBackend }));
}

const recorded = (ownerPid) => fs.existsSync(backendRecordPath(gaiaDir, ownerPid));

describe("two copies of the app sharing ~/.gaia", () => {
  const INSTALLED = 1000;
  const DEV = 2000;

  test("the second copy's startup leaves the first copy's live backend alone", async () => {
    recordBackend({ gaiaDir, ownerPid: DEV, backendPid: 8001 });

    const { killBackend, kept } = await cleanupAs(INSTALLED, { alive: [DEV, INSTALLED] });

    expect(killBackend).not.toHaveBeenCalled();
    expect(kept).toEqual([8001]);
    expect(recorded(DEV)).toBe(true);
  });

  test("the issue's sequence: neither copy's restart kills the other's backend", async () => {
    // Installed app starts while the dev copy runs, then records its own backend.
    recordBackend({ gaiaDir, ownerPid: DEV, backendPid: 8001 });
    let run = await cleanupAs(INSTALLED, { alive: [DEV, INSTALLED] });
    expect(run.killBackend).not.toHaveBeenCalled();
    recordBackend({ gaiaDir, ownerPid: INSTALLED, backendPid: 8611 });

    // The dev copy restarts its backend: the installed app's must survive.
    run = await cleanupAs(DEV, { alive: [DEV, INSTALLED], backends: [8001, 8611] });
    expect(run.killBackend).not.toHaveBeenCalledWith(8611);
    expect(recorded(INSTALLED)).toBe(true);
    recordBackend({ gaiaDir, ownerPid: DEV, backendPid: 8002 });

    // Both records stand side by side; neither overwrote the other.
    expect(fs.readFileSync(backendRecordPath(gaiaDir, INSTALLED), "utf8")).toBe("8611");
    expect(fs.readFileSync(backendRecordPath(gaiaDir, DEV), "utf8")).toBe("8002");
  });

  test("once the first copy dies, the next launch stops its orphaned backend", async () => {
    recordBackend({ gaiaDir, ownerPid: DEV, backendPid: 8001 });
    recordBackend({ gaiaDir, ownerPid: INSTALLED, backendPid: 8611 });

    const { killBackend, killed } = await cleanupAs(3000, { alive: [INSTALLED, 3000] });

    expect(killBackend).toHaveBeenCalledTimes(1);
    expect(killBackend).toHaveBeenCalledWith(8001);
    expect(killed).toEqual([8001]);
    expect(recorded(DEV)).toBe(false);
    expect(recorded(INSTALLED)).toBe(true);
  });

  test("a backend's exit removes only its own app's record", () => {
    recordBackend({ gaiaDir, ownerPid: DEV, backendPid: 8001 });
    recordBackend({ gaiaDir, ownerPid: INSTALLED, backendPid: 8611 });

    expect(forgetBackend({ gaiaDir, ownerPid: INSTALLED, backendPid: 8611 })).toBe(true);
    expect(recorded(INSTALLED)).toBe(false);
    expect(recorded(DEV)).toBe(true);
  });
});

describe("cleanupOrphanedBackends", () => {
  test("no records at all is a no-op", async () => {
    const { killBackend, killed, kept } = await cleanupAs(1);
    expect(killBackend).not.toHaveBeenCalled();
    expect(killed).toEqual([]);
    expect(kept).toEqual([]);
  });

  test("this app's own leftover record is cleaned, even though this app is alive", async () => {
    recordBackend({ gaiaDir, ownerPid: 1, backendPid: 9001 });
    const { killBackend } = await cleanupAs(1, { alive: [1] });
    expect(killBackend).toHaveBeenCalledWith(9001);
    expect(recorded(1)).toBe(false);
  });

  test("a recorded pid that is no longer a GAIA backend is never signalled", async () => {
    recordBackend({ gaiaDir, ownerPid: 5, backendPid: 9001 });
    const { killBackend } = await cleanupAs(1, { backends: [] });
    expect(killBackend).not.toHaveBeenCalled();
    expect(recorded(5)).toBe(false);
  });

  test("an identity probe that throws counts as not a backend", async () => {
    recordBackend({ gaiaDir, ownerPid: 5, backendPid: 9001 });
    const killBackend = jest.fn(async () => {});
    await cleanupOrphanedBackends({
      gaiaDir,
      selfPid: 1,
      killBackend,
      isBackend: () => {
        throw new Error("probe failed");
      },
      isAlive: () => false,
      logger: quiet,
    });
    expect(killBackend).not.toHaveBeenCalled();
  });

  test("a failed kill is logged and the next record is still handled", async () => {
    recordBackend({ gaiaDir, ownerPid: 5, backendPid: 9001 });
    recordBackend({ gaiaDir, ownerPid: 6, backendPid: 9002 });
    const warn = jest.fn();
    const killBackend = jest.fn(async (pid) => {
      if (pid === 9001) throw new Error("EPERM");
    });
    const { killed } = await cleanupOrphanedBackends({
      gaiaDir,
      selfPid: 1,
      killBackend,
      isBackend: () => true,
      isAlive: () => false,
      logger: { log: () => {}, warn },
    });
    expect(killBackend).toHaveBeenCalledTimes(2);
    expect(killed).toEqual([9002]);
    expect(warn.mock.calls.join("\n")).toMatch(/9001.*EPERM/);
  });

  test("a live app pid whose recorded backend is gone (pid reused) loses its record, nothing is killed", async () => {
    recordBackend({ gaiaDir, ownerPid: 5, backendPid: 9001 });
    const { killBackend, kept } = await cleanupAs(1, { alive: [5], backends: [] });
    expect(killBackend).not.toHaveBeenCalled();
    expect(kept).toEqual([]);
    expect(recorded(5)).toBe(false);
  });

  test("a live app's record is kept when its backend can't be verified", async () => {
    recordBackend({ gaiaDir, ownerPid: 5, backendPid: 9001 });
    const killBackend = jest.fn(async () => {});
    const { kept } = await cleanupOrphanedBackends({
      gaiaDir,
      selfPid: 1,
      killBackend,
      isBackend: () => {
        throw new Error("probe failed");
      },
      isAlive: () => true,
      logger: quiet,
    });
    expect(killBackend).not.toHaveBeenCalled();
    expect(kept).toEqual([9001]);
    expect(recorded(5)).toBe(true);
  });

  test("the legacy backend.pid from an older app is still cleaned up", async () => {
    fs.writeFileSync(path.join(gaiaDir, LEGACY_PIDFILE), "7777\n");
    const { killBackend } = await cleanupAs(1);
    expect(killBackend).toHaveBeenCalledWith(7777);
    expect(fs.existsSync(path.join(gaiaDir, LEGACY_PIDFILE))).toBe(false);
  });

  test("odd entries: stray files are ignored, empty records go only when their app is gone", async () => {
    const dir = path.join(gaiaDir, PIDFILE_DIR);
    fs.mkdirSync(dir);
    fs.writeFileSync(path.join(dir, "notes.txt"), "1234");
    fs.writeFileSync(path.join(dir, "12x.pid"), "1234");
    fs.writeFileSync(path.join(dir, "40.pid"), "");
    fs.writeFileSync(path.join(dir, "41.pid"), "");

    const { killBackend, kept } = await cleanupAs(1, { alive: [41] });

    expect(killBackend).not.toHaveBeenCalled();
    expect(kept).toEqual([]);
    expect(fs.existsSync(path.join(dir, "notes.txt"))).toBe(true);
    expect(fs.existsSync(path.join(dir, "12x.pid"))).toBe(true);
    expect(fs.existsSync(path.join(dir, "40.pid"))).toBe(false);
    expect(fs.existsSync(path.join(dir, "41.pid"))).toBe(true);
  });
});

describe("forgetBackend", () => {
  test("an older backend's exit leaves the newer backend's record in place", () => {
    recordBackend({ gaiaDir, ownerPid: 1, backendPid: 100 });
    recordBackend({ gaiaDir, ownerPid: 1, backendPid: 101 });
    expect(forgetBackend({ gaiaDir, ownerPid: 1, backendPid: 100 })).toBe(false);
    expect(fs.readFileSync(backendRecordPath(gaiaDir, 1), "utf8")).toBe("101");
  });

  test("no record is fine", () => {
    expect(forgetBackend({ gaiaDir, ownerPid: 1, backendPid: 100 })).toBe(false);
  });

  test("an unreadable record raises instead of passing for removed", () => {
    const failing = {
      readFileSync: () => {
        throw Object.assign(new Error("EACCES: permission denied"), { code: "EACCES" });
      },
    };
    expect(() => forgetBackend({ gaiaDir, ownerPid: 1, backendPid: 100, fs: failing })).toThrow(/EACCES/);
  });
});

describe("main.cjs uses the per-app records", () => {
  const main = fs
    .readFileSync(path.join(__dirname, "..", "..", "src", "gaia", "apps", "webui", "main.cjs"), "utf8")
    .replace(/\r\n/g, "\n");

  test("it never reads or writes the shared backend.pid itself", () => {
    expect(main).not.toContain('"backend.pid"');
  });

  test("its backend is recorded and forgotten under this app's own pid", () => {
    expect(main).toMatch(/recordBackend\(\{ gaiaDir: _GAIA_DIR, ownerPid: process\.pid, backendPid: child\.pid \}\)/);
    expect(main).toMatch(/forgetBackend\(\{ gaiaDir: _GAIA_DIR, ownerPid: process\.pid, backendPid: child\.pid \}\)/);
  });
});
