// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * Tests for agent-seeder
 * (src/gaia/apps/webui/services/agent-seeder.cjs)
 *
 * Covers: idempotency, sentinel-based skip, user-owned directory protection,
 * partial-copy recovery, cross-platform resourcesPath construction, missing
 * resourcesPath guard, and per-agent error isolation.
 *
 * All tests use a fresh tmpdir for both HOME (so ~/.gaia writes land in the
 * temp sandbox) and for process.resourcesPath, so nothing touches the real
 * filesystem outside os.tmpdir().
 */

const fs = require("fs");
const path = require("path");
const os = require("os");

// ── Test sandbox ─────────────────────────────────────────────────────────

/**
 * Build an isolated sandbox with:
 *   - a fake HOME that os.homedir() returns
 *   - a fake resources dir that we point process.resourcesPath at
 *
 * Each call creates a unique tmpdir so tests never collide.
 */
function makeSandbox() {
  const base = fs.mkdtempSync(path.join(os.tmpdir(), "gaia-seeder-test-"));
  const fakeHome = path.join(base, "home");
  const fakeResources = path.join(base, "resources");
  fs.mkdirSync(fakeHome, { recursive: true });
  fs.mkdirSync(fakeResources, { recursive: true });
  return { base, fakeHome, fakeResources };
}

/**
 * Populate `<resources>/agents/<id>/` with a handful of files so the seeder
 * has something real to copy. Returns the agent dir path.
 */
function createBundledAgent(resourcesDir, id, files = { "manifest.json": "{}" }) {
  const agentDir = path.join(resourcesDir, "agents", id);
  fs.mkdirSync(agentDir, { recursive: true });
  for (const [name, content] of Object.entries(files)) {
    const p = path.join(agentDir, name);
    fs.mkdirSync(path.dirname(p), { recursive: true });
    fs.writeFileSync(p, content);
  }
  return agentDir;
}

/**
 * Load the seeder module fresh after stubbing os.homedir and
 * process.resourcesPath. We use jest.isolateModules so each test gets a
 * clean require cache (the seeder caches nothing, but this keeps the
 * tests hermetic).
 */
function loadSeederWith({ fakeHome, resourcesPath }) {
  let seeder;
  jest.isolateModules(() => {
    // Stub os.homedir BEFORE requiring the seeder. The seeder reads it
    // at call time, not at require time, so stubbing after would also
    // work — but doing it here makes the intent clear.
    jest.spyOn(os, "homedir").mockReturnValue(fakeHome);

    // process.resourcesPath is normally set by Electron at launch. Tests
    // drive it directly.
    Object.defineProperty(process, "resourcesPath", {
      configurable: true,
      writable: true,
      value: resourcesPath,
    });

    // eslint-disable-next-line global-require
    seeder = require("../../src/gaia/apps/webui/services/agent-seeder.cjs");
  });
  return seeder;
}

function restoreEnv() {
  jest.restoreAllMocks();
  // Leave process.resourcesPath alone — the next test sets it again. We
  // only need to ensure the descriptor is configurable, which we did above.
}

// ── Tests ────────────────────────────────────────────────────────────────

describe("agent-seeder", () => {
  afterEach(() => {
    restoreEnv();
  });

  test("idempotency — second call skips already-seeded agents", async () => {
    const { fakeHome, fakeResources } = makeSandbox();
    createBundledAgent(fakeResources, "alpha", {
      "manifest.json": JSON.stringify({ name: "alpha" }),
      "code/main.py": "print('hi')",
    });

    const seeder = loadSeederWith({ fakeHome, resourcesPath: fakeResources });

    const first = await seeder.seedBundledAgents();
    expect(first.seeded).toEqual(["alpha"]);
    expect(first.skipped).toEqual([]);
    expect(first.errors).toEqual([]);

    // Sentinel should exist.
    const sentinel = path.join(fakeHome, ".gaia", "agents", "alpha", ".seeded");
    expect(fs.existsSync(sentinel)).toBe(true);

    // Content copied.
    const manifest = path.join(fakeHome, ".gaia", "agents", "alpha", "manifest.json");
    expect(fs.readFileSync(manifest, "utf8")).toBe(
      JSON.stringify({ name: "alpha" })
    );

    const second = await seeder.seedBundledAgents();
    expect(second.seeded).toEqual([]);
    expect(second.skipped).toEqual(["alpha"]);
    expect(second.errors).toEqual([]);
  });

  test("skip when .seeded present (pre-existing sentinel)", async () => {
    const { fakeHome, fakeResources } = makeSandbox();
    createBundledAgent(fakeResources, "beta");

    // Pre-populate the target with just the sentinel (pretend a previous
    // run already seeded it).
    const target = path.join(fakeHome, ".gaia", "agents", "beta");
    fs.mkdirSync(target, { recursive: true });
    fs.writeFileSync(path.join(target, ".seeded"), "{}");

    const seeder = loadSeederWith({ fakeHome, resourcesPath: fakeResources });
    const result = await seeder.seedBundledAgents();

    expect(result.seeded).toEqual([]);
    expect(result.skipped).toEqual(["beta"]);
    expect(result.errors).toEqual([]);
  });

  test("skip user-owned directory (target exists WITHOUT sentinel)", async () => {
    const { fakeHome, fakeResources } = makeSandbox();
    createBundledAgent(fakeResources, "gamma", {
      "bundled-only.txt": "from installer",
    });

    // Simulate a hand-authored agent at the target — no .seeded sentinel.
    const target = path.join(fakeHome, ".gaia", "agents", "gamma");
    fs.mkdirSync(target, { recursive: true });
    fs.writeFileSync(path.join(target, "user-file.txt"), "do not clobber");

    const seeder = loadSeederWith({ fakeHome, resourcesPath: fakeResources });
    const result = await seeder.seedBundledAgents();

    expect(result.seeded).toEqual([]);
    expect(result.skipped).toEqual(["gamma"]);
    expect(result.errors).toEqual([]);

    // User file untouched.
    expect(
      fs.readFileSync(path.join(target, "user-file.txt"), "utf8")
    ).toBe("do not clobber");
    // Bundled file was NOT copied in.
    expect(fs.existsSync(path.join(target, "bundled-only.txt"))).toBe(false);
    // No sentinel magically appeared.
    expect(fs.existsSync(path.join(target, ".seeded"))).toBe(false);
  });

  test("partial-copy recovery — stale <id>.partial cleaned up", async () => {
    const { fakeHome, fakeResources } = makeSandbox();
    createBundledAgent(fakeResources, "delta", {
      "manifest.json": "{}",
    });

    // Simulate a prior failed run: <id>.partial exists with leftover data.
    const agentsRoot = path.join(fakeHome, ".gaia", "agents");
    fs.mkdirSync(agentsRoot, { recursive: true });
    const partial = path.join(agentsRoot, "delta.partial");
    fs.mkdirSync(partial, { recursive: true });
    fs.writeFileSync(path.join(partial, "garbage.txt"), "from failed run");

    const seeder = loadSeederWith({ fakeHome, resourcesPath: fakeResources });
    const result = await seeder.seedBundledAgents();

    expect(result.seeded).toEqual(["delta"]);
    expect(result.errors).toEqual([]);

    // Partial dir was cleaned up.
    expect(fs.existsSync(partial)).toBe(false);

    // Target has only the bundled content, not the stale "garbage.txt".
    const target = path.join(agentsRoot, "delta");
    expect(fs.existsSync(path.join(target, "manifest.json"))).toBe(true);
    expect(fs.existsSync(path.join(target, "garbage.txt"))).toBe(false);
    expect(fs.existsSync(path.join(target, ".seeded"))).toBe(true);
  });

  describe("cross-platform resourcesPath construction", () => {
    // We exercise the real filesystem under each fixture path structure
    // so the test doubles as an integration check of path.join semantics.
    // Each fixture uses a tmpdir with a subdir that mimics the shape of
    // the platform's resources location.
    const fixtures = [
      {
        name: "Windows-style",
        // Simulates: C:\Program Files\GAIA\resources
        suffix: path.join("ProgramFiles", "GAIA", "resources"),
      },
      {
        name: "macOS-style",
        // Simulates: .../GAIA.app/Contents/Resources
        suffix: path.join("GAIA.app", "Contents", "Resources"),
      },
      {
        name: "Linux-style",
        // Simulates: /opt/gaia/resources
        suffix: path.join("opt", "gaia", "resources"),
      },
    ];

    for (const fx of fixtures) {
      test(`constructs agents/ source correctly for ${fx.name}`, async () => {
        const { base, fakeHome } = makeSandbox();
        const resourcesPath = path.join(base, fx.suffix);
        fs.mkdirSync(path.join(resourcesPath, "agents"), { recursive: true });
        // Empty agents/ dir is fine — the seeder should walk it and return.

        const seeder = loadSeederWith({ fakeHome, resourcesPath });
        const result = await seeder.seedBundledAgents();

        expect(result.seeded).toEqual([]);
        expect(result.skipped).toEqual([]);
        expect(result.errors).toEqual([]);

        // Sanity: drop in an agent at the constructed path and re-run.
        createBundledAgent(resourcesPath, "platformcheck");
        const second = await seeder.seedBundledAgents();
        expect(second.seeded).toEqual(["platformcheck"]);
      });
    }
  });

  test("missing process.resourcesPath returns empty result without throwing", async () => {
    const { fakeHome } = makeSandbox();
    const seeder = loadSeederWith({ fakeHome, resourcesPath: undefined });

    const result = await seeder.seedBundledAgents();
    expect(result).toEqual({ seeded: [], skipped: [], errors: [] });
  });

  test("missing agents/ directory returns empty result (not an error)", async () => {
    const { fakeHome, fakeResources } = makeSandbox();
    // Deliberately do NOT create <resources>/agents.

    const seeder = loadSeederWith({ fakeHome, resourcesPath: fakeResources });
    const result = await seeder.seedBundledAgents();

    expect(result).toEqual({ seeded: [], skipped: [], errors: [] });
  });

  test("error isolation — one failing agent does not block others", async () => {
    const { fakeHome, fakeResources } = makeSandbox();
    createBundledAgent(fakeResources, "good1", { "manifest.json": "{}" });
    createBundledAgent(fakeResources, "bad", { "manifest.json": "{}" });
    createBundledAgent(fakeResources, "good2", { "manifest.json": "{}" });

    const seeder = loadSeederWith({ fakeHome, resourcesPath: fakeResources });

    // Force a failure for the "bad" agent only. We spy on fs.renameSync
    // (the atomic-rename step) and throw when the source path ends with
    // "bad.partial". All other renames go through to the real impl.
    const realRename = fs.renameSync.bind(fs);
    const renameSpy = jest
      .spyOn(fs, "renameSync")
      .mockImplementation((from, to) => {
        if (typeof from === "string" && from.endsWith(`bad.partial`)) {
          const err = new Error("EACCES: simulated permission denied");
          err.code = "EACCES";
          throw err;
        }
        return realRename(from, to);
      });

    const result = await seeder.seedBundledAgents();

    // Cleanup spy so other tests are unaffected.
    renameSpy.mockRestore();

    expect(result.seeded.sort()).toEqual(["good1", "good2"]);
    expect(result.errors).toHaveLength(1);
    expect(result.errors[0].id).toBe("bad");
    expect(result.errors[0].error).toBeInstanceOf(Error);
    expect(result.errors[0].error.message).toMatch(/EACCES/);

    // The failing agent's target should NOT exist (since rename failed).
    const badTarget = path.join(fakeHome, ".gaia", "agents", "bad");
    expect(fs.existsSync(badTarget)).toBe(false);
    // And the partial should have been cleaned up.
    const badPartial = path.join(fakeHome, ".gaia", "agents", "bad.partial");
    expect(fs.existsSync(badPartial)).toBe(false);

    // The good agents DID land, with sentinels.
    expect(
      fs.existsSync(path.join(fakeHome, ".gaia", "agents", "good1", ".seeded"))
    ).toBe(true);
    expect(
      fs.existsSync(path.join(fakeHome, ".gaia", "agents", "good2", ".seeded"))
    ).toBe(true);
  });

  test("logs are written to ~/.gaia/logs/seeder.log", async () => {
    const { fakeHome, fakeResources } = makeSandbox();
    createBundledAgent(fakeResources, "loggy");

    const seeder = loadSeederWith({ fakeHome, resourcesPath: fakeResources });
    await seeder.seedBundledAgents();

    const logPath = path.join(fakeHome, ".gaia", "logs", "seeder.log");
    expect(fs.existsSync(logPath)).toBe(true);
    const content = fs.readFileSync(logPath, "utf8");
    expect(content).toMatch(/\[INFO\]/);
    expect(content).toMatch(/loggy/);
  });

  test("non-directory entries in agents/ are ignored", async () => {
    const { fakeHome, fakeResources } = makeSandbox();
    const agentsSrc = path.join(fakeResources, "agents");
    fs.mkdirSync(agentsSrc, { recursive: true });
    // Create a loose file alongside a real agent dir.
    fs.writeFileSync(path.join(agentsSrc, "README.txt"), "ignore me");
    createBundledAgent(fakeResources, "real");

    const seeder = loadSeederWith({ fakeHome, resourcesPath: fakeResources });
    const result = await seeder.seedBundledAgents();

    expect(result.seeded).toEqual(["real"]);
    expect(result.skipped).toEqual([]);
    expect(result.errors).toEqual([]);
  });
});

// ── Marker-based deletion honoring + legacy zoo-agent cleanup ────────────
//
// Contract under test (issue #1908):
//   - A per-agent marker file at `<home>/.gaia/seeder/<id>.seeded` records
//     "this machine seeded <id> once". Its EXISTENCE is the signal — content
//     is informational only and never asserted on here.
//   - Marker present → never re-seed, even after the user deletes the agent.
//   - A legacy `~/.gaia/agents/zoo-agent` left by older installs is cleaned
//     up once (guarded: sentinel required, no user modifications, never
//     through a symlink) and reported via the new additive `cleaned` array.

describe("marker-based deletion honoring + legacy cleanup", () => {
  afterEach(() => {
    restoreEnv();
  });

  function markerPath(fakeHome, id) {
    return path.join(fakeHome, ".gaia", "seeder", `${id}.seeded`);
  }

  function writeMarker(fakeHome, id) {
    fs.mkdirSync(path.join(fakeHome, ".gaia", "seeder"), { recursive: true });
    fs.writeFileSync(
      markerPath(fakeHome, id),
      JSON.stringify({ seededAt: new Date().toISOString(), source: "test" })
    );
  }

  function agentDirPath(fakeHome, id) {
    return path.join(fakeHome, ".gaia", "agents", id);
  }

  /**
   * Ensure `<resources>/agents/` exists even when a test bundles nothing,
   * so the seeder proceeds past its missing-source guard and cleanup runs.
   */
  function ensureBundleRoot(fakeResources) {
    fs.mkdirSync(path.join(fakeResources, "agents"), { recursive: true });
  }

  /**
   * Plant a legacy seeded zoo-agent at `<home>/.gaia/agents/zoo-agent` with
   * a valid `.seeded` sentinel whose `seededAt` lies one hour in the past.
   * Every file's mtime is pinned to `seededAt` (unmodified-since-seed shape).
   * Returns { dir, seededAt }.
   */
  function createLegacyZooAgent(fakeHome) {
    const seededAt = new Date(Date.now() - 60 * 60 * 1000);
    const dir = agentDirPath(fakeHome, "zoo-agent");
    fs.mkdirSync(dir, { recursive: true });
    fs.writeFileSync(path.join(dir, "agent.py"), "print('zoo')\n");
    fs.writeFileSync(path.join(dir, "manifest.json"), "{}");
    fs.writeFileSync(
      path.join(dir, ".seeded"),
      JSON.stringify({ seededAt: seededAt.toISOString(), source: "test" })
    );
    for (const name of ["agent.py", "manifest.json", ".seeded"]) {
      fs.utimesSync(path.join(dir, name), seededAt, seededAt);
    }
    fs.utimesSync(dir, seededAt, seededAt);
    return { dir, seededAt };
  }

  test("AC1 fresh install — seeds example-agent, writes sentinel and marker", async () => {
    const { fakeHome, fakeResources } = makeSandbox();
    createBundledAgent(fakeResources, "example-agent", {
      "agent.py": "class ExampleAgent: pass\n",
    });

    const seeder = loadSeederWith({ fakeHome, resourcesPath: fakeResources });
    const result = await seeder.seedBundledAgents();

    expect(result.seeded).toEqual(["example-agent"]);
    expect(result.errors).toEqual([]);

    const target = agentDirPath(fakeHome, "example-agent");
    expect(fs.existsSync(path.join(target, "agent.py"))).toBe(true);
    expect(fs.existsSync(path.join(target, ".seeded"))).toBe(true);
    expect(fs.existsSync(markerPath(fakeHome, "example-agent"))).toBe(true);
  });

  test("AC2 deletion honored — deleted agent is never re-seeded while marker survives", async () => {
    const { fakeHome, fakeResources } = makeSandbox();
    createBundledAgent(fakeResources, "example-agent");

    const seeder = loadSeederWith({ fakeHome, resourcesPath: fakeResources });
    const first = await seeder.seedBundledAgents();
    expect(first.seeded).toEqual(["example-agent"]);

    // User deletes the seeded agent (marker left in place).
    const target = agentDirPath(fakeHome, "example-agent");
    fs.rmSync(target, { recursive: true, force: true });

    const second = await seeder.seedBundledAgents();
    expect(second.seeded).toEqual([]);
    expect(second.skipped).toContain("example-agent");
    expect(fs.existsSync(target)).toBe(false);
    expect(fs.existsSync(markerPath(fakeHome, "example-agent"))).toBe(true);
  });

  test("AC3 back-fill — pre-marker install gets a marker, then deletion is honored", async () => {
    const { fakeHome, fakeResources } = makeSandbox();
    createBundledAgent(fakeResources, "backfill-agent");

    // Simulate an install seeded before markers existed: dir + sentinel,
    // no marker anywhere.
    const target = agentDirPath(fakeHome, "backfill-agent");
    fs.mkdirSync(target, { recursive: true });
    fs.writeFileSync(path.join(target, "agent.py"), "original\n");
    fs.writeFileSync(
      path.join(target, ".seeded"),
      JSON.stringify({ seededAt: new Date().toISOString(), source: "test" })
    );

    const seeder = loadSeederWith({ fakeHome, resourcesPath: fakeResources });
    const first = await seeder.seedBundledAgents();

    expect(first.seeded).toEqual([]);
    expect(first.skipped).toContain("backfill-agent");
    // Marker back-filled from the sentinel.
    expect(fs.existsSync(markerPath(fakeHome, "backfill-agent"))).toBe(true);
    // Contents untouched.
    expect(fs.readFileSync(path.join(target, "agent.py"), "utf8")).toBe("original\n");

    // Now the user deletes it — must stay deleted.
    fs.rmSync(target, { recursive: true, force: true });
    const second = await seeder.seedBundledAgents();
    expect(second.seeded).toEqual([]);
    expect(fs.existsSync(target)).toBe(false);
    expect(fs.existsSync(markerPath(fakeHome, "backfill-agent"))).toBe(true);
  });

  test("AC4 marker precedence — user-recreated dir under a marked id is never touched", async () => {
    const { fakeHome, fakeResources } = makeSandbox();
    createBundledAgent(fakeResources, "precedence-agent", {
      "bundled-only.txt": "from installer",
    });

    // Prior seed+delete cycle left a marker; the user then hand-created
    // their OWN agent under the same id (no sentinel).
    writeMarker(fakeHome, "precedence-agent");
    const target = agentDirPath(fakeHome, "precedence-agent");
    fs.mkdirSync(target, { recursive: true });
    fs.writeFileSync(path.join(target, "user-file.txt"), "mine now");

    const seeder = loadSeederWith({ fakeHome, resourcesPath: fakeResources });
    const result = await seeder.seedBundledAgents();

    expect(result.seeded).toEqual([]);
    expect(result.skipped).toContain("precedence-agent");
    expect(result.errors).toEqual([]);

    // The user's dir is exactly as they left it.
    expect(fs.readFileSync(path.join(target, "user-file.txt"), "utf8")).toBe("mine now");
    expect(fs.existsSync(path.join(target, "bundled-only.txt"))).toBe(false);
    expect(fs.existsSync(path.join(target, ".seeded"))).toBe(false);
  });

  test("AC5 user-owned — no sentinel, no marker: untouched, and NO marker is created", async () => {
    const { fakeHome, fakeResources } = makeSandbox();
    createBundledAgent(fakeResources, "user-owned-agent", {
      "bundled-only.txt": "from installer",
    });

    const target = agentDirPath(fakeHome, "user-owned-agent");
    fs.mkdirSync(target, { recursive: true });
    fs.writeFileSync(path.join(target, "user-file.txt"), "hands off");

    const seeder = loadSeederWith({ fakeHome, resourcesPath: fakeResources });

    for (let run = 0; run < 2; run += 1) {
      const result = await seeder.seedBundledAgents();
      expect(result.seeded).toEqual([]);
      expect(result.skipped).toContain("user-owned-agent");
      expect(result.errors).toEqual([]);
      expect(fs.readFileSync(path.join(target, "user-file.txt"), "utf8")).toBe("hands off");
      expect(fs.existsSync(path.join(target, "bundled-only.txt"))).toBe(false);
      expect(fs.existsSync(path.join(target, ".seeded"))).toBe(false);
      // Crucially: a user-owned dir must NOT acquire a marker.
      expect(fs.existsSync(markerPath(fakeHome, "user-owned-agent"))).toBe(false);
    }
  });

  test("AC6 legacy cleanup — unmodified seeded zoo-agent is removed exactly once", async () => {
    const { fakeHome, fakeResources } = makeSandbox();
    ensureBundleRoot(fakeResources); // zoo-agent is NOT bundled anymore
    const { dir } = createLegacyZooAgent(fakeHome);

    const seeder = loadSeederWith({ fakeHome, resourcesPath: fakeResources });
    const first = await seeder.seedBundledAgents();

    expect(first.cleaned).toEqual(["zoo-agent"]);
    expect(fs.existsSync(dir)).toBe(false);

    const second = await seeder.seedBundledAgents();
    expect(second.cleaned).toEqual([]);
    expect(fs.existsSync(dir)).toBe(false);
  });

  test("AC7 legacy cleanup — user-modified zoo-agent is preserved", async () => {
    const { fakeHome, fakeResources } = makeSandbox();
    ensureBundleRoot(fakeResources);
    const { dir, seededAt } = createLegacyZooAgent(fakeHome);

    // One file modified well past seededAt + 5s slack → user's work now.
    const late = new Date(seededAt.getTime() + 60 * 1000);
    fs.utimesSync(path.join(dir, "agent.py"), late, late);

    const seeder = loadSeederWith({ fakeHome, resourcesPath: fakeResources });
    const result = await seeder.seedBundledAgents();

    expect(result.cleaned).toEqual([]);
    expect(fs.existsSync(dir)).toBe(true);
    expect(fs.existsSync(path.join(dir, "agent.py"))).toBe(true);
  });

  const symlinkTest = process.platform === "win32" ? test.skip : test;
  symlinkTest("AC8 legacy cleanup — never deletes through a symlink", async () => {
    const { base, fakeHome, fakeResources } = makeSandbox();
    ensureBundleRoot(fakeResources);

    // A real directory elsewhere, reached via a symlink at the zoo-agent path.
    const linkTarget = path.join(base, "link-target");
    fs.mkdirSync(linkTarget, { recursive: true });
    fs.writeFileSync(path.join(linkTarget, "precious.txt"), "do not delete");
    fs.writeFileSync(
      path.join(linkTarget, ".seeded"),
      JSON.stringify({
        seededAt: new Date(Date.now() - 60 * 60 * 1000).toISOString(),
        source: "test",
      })
    );

    fs.mkdirSync(path.join(fakeHome, ".gaia", "agents"), { recursive: true });
    fs.symlinkSync(linkTarget, agentDirPath(fakeHome, "zoo-agent"), "dir");

    const seeder = loadSeederWith({ fakeHome, resourcesPath: fakeResources });
    const result = await seeder.seedBundledAgents();

    expect(result.cleaned).toEqual([]);
    // Nothing behind the link was harmed.
    expect(fs.existsSync(path.join(linkTarget, "precious.txt"))).toBe(true);
    expect(fs.readFileSync(path.join(linkTarget, "precious.txt"), "utf8")).toBe(
      "do not delete"
    );
  });

  test("AC9 cleanup failure isolation — EPERM on rmSync does not block seeding", async () => {
    const { fakeHome, fakeResources } = makeSandbox();
    createBundledAgent(fakeResources, "goodagent");
    const { dir } = createLegacyZooAgent(fakeHome);

    // Simulate a Windows-style file lock: rmSync throws only for zoo-agent
    // paths; every other call falls through to the real implementation.
    const realRm = fs.rmSync.bind(fs);
    const rmSpy = jest.spyOn(fs, "rmSync").mockImplementation((target, opts) => {
      if (typeof target === "string" && target.includes("zoo-agent")) {
        const err = new Error("EPERM: simulated locked file");
        err.code = "EPERM";
        throw err;
      }
      return realRm(target, opts);
    });

    const seeder = loadSeederWith({ fakeHome, resourcesPath: fakeResources });
    const result = await seeder.seedBundledAgents();

    rmSpy.mockRestore();

    // No throw escaped, cleanup reported nothing, and seeding proceeded.
    expect(result.cleaned).toEqual([]);
    expect(result.seeded).toContain("goodagent");
    expect(fs.existsSync(dir)).toBe(true); // left for retry next launch
    expect(
      fs.existsSync(path.join(agentDirPath(fakeHome, "goodagent"), ".seeded"))
    ).toBe(true);
  });

  test("AC10 upgrade in one pass — zoo cleaned AND example-agent seeded in a single run", async () => {
    const { fakeHome, fakeResources } = makeSandbox();
    createBundledAgent(fakeResources, "example-agent", {
      "agent.py": "class ExampleAgent: pass\n",
    });
    const { dir: zooDir } = createLegacyZooAgent(fakeHome);

    const seeder = loadSeederWith({ fakeHome, resourcesPath: fakeResources });
    const result = await seeder.seedBundledAgents();

    expect(result.cleaned).toEqual(["zoo-agent"]);
    expect(result.seeded).toEqual(["example-agent"]);
    expect(fs.existsSync(zooDir)).toBe(false);

    const target = agentDirPath(fakeHome, "example-agent");
    expect(fs.existsSync(path.join(target, ".seeded"))).toBe(true);
    expect(fs.existsSync(markerPath(fakeHome, "example-agent"))).toBe(true);
  });

  test("AC11 reset semantics — agents/ wipe stays deleted; seeder/ wipe re-seeds", async () => {
    const { fakeHome, fakeResources } = makeSandbox();
    createBundledAgent(fakeResources, "example-agent");

    const seeder = loadSeederWith({ fakeHome, resourcesPath: fakeResources });
    const first = await seeder.seedBundledAgents();
    expect(first.seeded).toEqual(["example-agent"]);

    // Wipe ~/.gaia/agents entirely — markers live outside it and survive.
    fs.rmSync(path.join(fakeHome, ".gaia", "agents"), {
      recursive: true,
      force: true,
    });

    const second = await seeder.seedBundledAgents();
    expect(second.seeded).toEqual([]);
    expect(second.skipped).toContain("example-agent");
    expect(fs.existsSync(agentDirPath(fakeHome, "example-agent"))).toBe(false);

    // Deleting the marker root is the documented recovery path.
    fs.rmSync(path.join(fakeHome, ".gaia", "seeder"), {
      recursive: true,
      force: true,
    });

    const third = await seeder.seedBundledAgents();
    expect(third.seeded).toEqual(["example-agent"]);
    expect(
      fs.existsSync(path.join(agentDirPath(fakeHome, "example-agent"), ".seeded"))
    ).toBe(true);
  });

  test("AC12 identity — bundled example-agent exists and no zoo persona remains", () => {
    const bundledRoot = path.join(
      __dirname,
      "..",
      "..",
      "src",
      "gaia",
      "apps",
      "webui",
      "build",
      "bundled-agents"
    );

    const agentPy = path.join(bundledRoot, "example-agent", "agent.py");
    expect(fs.existsSync(agentPy)).toBe(true);

    const content = fs.readFileSync(agentPy, "utf8");
    expect(content).toContain("class ExampleAgent");
    expect(content).toContain('AGENT_ID = "example-agent"');
    expect(content).toContain('AGENT_NAME = "Example Agent"');

    // No file anywhere under bundled-agents/ may carry the zoo persona.
    function walkFiles(root) {
      const out = [];
      for (const entry of fs.readdirSync(root, { withFileTypes: true })) {
        const p = path.join(root, entry.name);
        if (entry.isDirectory()) out.push(...walkFiles(p));
        else if (entry.isFile()) out.push(p);
      }
      return out;
    }

    const offenders = walkFiles(bundledRoot).filter((p) =>
      /zoo/i.test(fs.readFileSync(p, "utf8"))
    );
    expect(offenders).toEqual([]);
  });
});
