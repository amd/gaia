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
