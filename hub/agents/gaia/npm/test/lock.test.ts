// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
/**
 * The SHIPPED binaries.lock.json — every declared entry must be well-formed and
 * internally consistent, so a hand-edit or a bad CI regeneration is caught here
 * rather than as a 404 on a user's first run.
 *
 * The TUI half is the load-bearing part now: those entries point at the
 * `terminal-hub` lane, which THIS package does not publish. A wrong filename or
 * a wrong lane version is not a build failure anywhere — it is a 404 for the
 * user. These assertions are the only thing standing in front of that.
 */

import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import {
  COMPONENTS,
  SUPPORTED_SIDECAR_PLATFORMS,
  SUPPORTED_TUI_PLATFORMS,
  TUI_ARTIFACT_NAMES,
  componentBaseUrl,
  isPlaceholderSha,
  loadLock,
} from "../src/platform.js";

const pkgRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const LOCK_PATH = path.join(pkgRoot, "binaries.lock.json");
const lock = loadLock(LOCK_PATH);
const sidecar = lock.components["sidecar"]!;
const tui = lock.components["tui"]!;
const pkg = JSON.parse(readFileSync(path.join(pkgRoot, "package.json"), "utf8")) as {
  name: string;
  version: string;
  bin: Record<string, string>;
};

const EXPECTED_EXECUTABLE: Record<string, string> = {
  sidecar: "gaia-agent",
  tui: "gaia-tui",
};

describe("shipped binaries.lock.json", () => {
  it("parses with the two-lane schema", () => {
    expect(lock.schemaVersion).toMatch(/^3\./);
    expect(Object.keys(lock.components).sort()).toEqual([...COMPONENTS].sort());
  });

  it("agrees with package.json on the version", () => {
    expect(lock.agentVersion).toBe(pkg.version);
    expect(pkg.version).toBe("0.1.0");
  });

  it("publishes the sidecar from the gaia lane at the agent's own version", () => {
    expect(sidecar.componentVersion).toBe(lock.agentVersion);
    expect(componentBaseUrl(lock, "sidecar")).toBe(
      `https://hub.amd-gaia.ai/agents/gaia/${lock.agentVersion}`,
    );
  });

  it("consumes the TUI from the terminal-hub lane, not the gaia lane", () => {
    // The point of the whole component split: we do NOT republish the TUI under
    // our own version. A baseUrl that drifted back into agents/gaia/ would mean
    // someone reintroduced the duplicate build.
    expect(componentBaseUrl(lock, "tui")).toBe(
      `https://hub.amd-gaia.ai/agents/terminal-hub/${tui.componentVersion}`,
    );
    expect(componentBaseUrl(lock, "tui")).not.toContain("/agents/gaia/");
  });

  it("pins a real semver for the terminal-hub component", () => {
    expect(tui.componentVersion).toMatch(/^\d+\.\d+\.\d+/);
  });

  it("gives each component its own base URL", () => {
    expect(componentBaseUrl(lock, "tui")).not.toBe(componentBaseUrl(lock, "sidecar"));
    // schemaVersion 2.0's single shared baseUrl must be gone, not merely unused —
    // a leftover would be a second, silently-stale source of truth.
    expect((lock as unknown as Record<string, unknown>)["baseUrl"]).toBeUndefined();
  });

  it("declares only platforms the package claims to support", () => {
    // A SUBSET, not equality: the release pipeline treats the darwin-x64 (Intel
    // Mac) sidecar freeze as best-effort and drops its entry when that runner
    // fails, so a partial release must still produce a lock this package accepts.
    for (const p of Object.keys(sidecar.platforms)) {
      expect(SUPPORTED_SIDECAR_PLATFORMS as readonly string[]).toContain(p);
    }
    for (const p of Object.keys(tui.platforms)) {
      expect(SUPPORTED_TUI_PLATFORMS as readonly string[]).toContain(p);
    }
  });

  it("always publishes the three required sidecar platforms", () => {
    // gaia-agent.yaml `requirements.platforms`. Only darwin-x64 is optional.
    for (const p of ["win32-x64", "linux-x64", "darwin-arm64"]) {
      expect(sidecar.platforms[p]).toBeDefined();
    }
  });

  it("covers every platform terminal-hub publishes for", () => {
    expect(Object.keys(tui.platforms).sort()).toEqual([...SUPPORTED_TUI_PLATFORMS].sort());
  });

  it("has no sidecar build for the arm64 platforms the TUI covers", () => {
    for (const p of ["linux-arm64", "win32-arm64"]) {
      expect(tui.platforms[p]).toBeDefined();
      expect(sidecar.platforms[p]).toBeUndefined();
    }
  });

  describe("component: sidecar", () => {
    for (const [platformKey, entry] of Object.entries(sidecar.platforms)) {
      it(`${platformKey} entry is well-formed and self-consistent`, () => {
        const isWin = platformKey.startsWith("win32");
        expect(entry.filename).toBe(`gaia-agent-${platformKey}${isWin ? ".exe" : ""}`);
        expect(entry.executable).toBe(`gaia-agent${isWin ? ".exe" : ""}`);
        expect(entry.filename.endsWith(".exe")).toBe(isWin);
        expect(typeof entry.size).toBe("number");
        if (!isPlaceholderSha(entry.sha256)) {
          expect(entry.sha256).toMatch(/^[0-9a-f]{64}$/);
        }
      });
    }
  });

  describe("component: tui", () => {
    for (const [platformKey, entry] of Object.entries(tui.platforms)) {
      it(`${platformKey} resolves to the terminal-hub artifact name`, () => {
        const isWin = platformKey.startsWith("win32");
        // terminal-hub names its Windows builds `win-*`, ours are keyed `win32-*`
        // (process.platform). The mapping lives in the filename, and this is
        // where the two spellings are proved to line up — release_components.yml
        // publishes exactly `gaia-<platform>[.exe]` for those six keys.
        expect(entry.filename).toBe(TUI_ARTIFACT_NAMES[platformKey]);
        expect(entry.filename).not.toContain("win32");
        expect(entry.filename.endsWith(".exe")).toBe(isWin);
        // Installed name is ours, not the hub's: the hub artifact is called
        // `gaia`, which would shadow the npm bin shim.
        expect(entry.executable).toBe(`gaia-tui${isWin ? ".exe" : ""}`);
        expect(typeof entry.size).toBe("number");
        if (!isPlaceholderSha(entry.sha256)) {
          expect(entry.sha256).toMatch(/^[0-9a-f]{64}$/);
        }
      });
    }
  });

  it("installs the TUI as gaia-tui, never as gaia", () => {
    // A cache-dir executable literally named `gaia` would shadow the npm bin shim —
    // and terminal-hub's artifacts ARE named `gaia-*`, so this is a live hazard.
    expect(Object.keys(pkg.bin)).toEqual(["gaia"]);
    for (const entry of Object.values(tui.platforms)) {
      expect(entry.executable.replace(/\.exe$/, "")).toBe("gaia-tui");
    }
  });

  it("gives the two components distinct executable names", () => {
    const sidecarExes = new Set(Object.values(sidecar.platforms).map((e) => e.executable));
    const tuiExes = new Set(Object.values(tui.platforms).map((e) => e.executable));
    for (const e of sidecarExes) expect(tuiExes.has(e)).toBe(false);
    expect(EXPECTED_EXECUTABLE["sidecar"]).not.toBe(EXPECTED_EXECUTABLE["tui"]);
  });
});
