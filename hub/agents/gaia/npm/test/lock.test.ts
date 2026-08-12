// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
/**
 * The SHIPPED binaries.lock.json — every declared entry must be well-formed and
 * internally consistent, so a hand-edit or a bad CI regeneration is caught here
 * rather than as a 404 on a user's first run.
 */

import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import {
  COMPONENTS,
  SUPPORTED_SIDECAR_PLATFORMS,
  SUPPORTED_TUI_PLATFORMS,
  isPlaceholderSha,
  loadLock,
} from "../src/platform.js";

const pkgRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const LOCK_PATH = path.join(pkgRoot, "binaries.lock.json");
const lock = loadLock(LOCK_PATH);
const pkg = JSON.parse(readFileSync(path.join(pkgRoot, "package.json"), "utf8")) as {
  name: string;
  version: string;
  bin: Record<string, string>;
};

// Per component: the artifact-filename stem published on the hub, and the
// basename it is installed as. They coincide today; keeping them separate keeps
// the assertions honest if one ever changes.
const EXPECTED_FILENAME_STEM: Record<string, string> = {
  sidecar: "gaia-agent",
  tui: "gaia-tui",
};
const EXPECTED_EXECUTABLE: Record<string, string> = {
  sidecar: "gaia-agent",
  tui: "gaia-tui",
};

describe("shipped binaries.lock.json", () => {
  it("parses with the dual-component schema", () => {
    expect(lock.schemaVersion).toMatch(/^2\./);
    expect(Object.keys(lock.components).sort()).toEqual([...COMPONENTS].sort());
  });

  it("agrees with package.json on the version", () => {
    expect(lock.agentVersion).toBe(pkg.version);
    expect(pkg.version).toBe("0.1.0");
  });

  it("points at the versioned hub path for this exact version", () => {
    expect(lock.baseUrl).toBe(`https://hub.amd-gaia.ai/agents/gaia/${lock.agentVersion}`);
  });

  it("declares only platforms the package claims to support", () => {
    // A SUBSET, not equality: the release pipeline treats the darwin-x64 (Intel
    // Mac) sidecar freeze as best-effort and drops its entry when that runner
    // fails, so a partial release must still produce a lock this package accepts.
    for (const p of Object.keys(lock.components["sidecar"]!)) {
      expect(SUPPORTED_SIDECAR_PLATFORMS as readonly string[]).toContain(p);
    }
    for (const p of Object.keys(lock.components["tui"]!)) {
      expect(SUPPORTED_TUI_PLATFORMS as readonly string[]).toContain(p);
    }
  });

  it("always publishes the three required sidecar platforms", () => {
    // gaia-agent.yaml `requirements.platforms`. Only darwin-x64 is optional.
    for (const p of ["win32-x64", "linux-x64", "darwin-arm64"]) {
      expect(lock.components["sidecar"]![p]).toBeDefined();
    }
  });

  it("publishes the TUI for every platform Go cross-compiles to", () => {
    expect(Object.keys(lock.components["tui"]!).sort()).toEqual(
      [...SUPPORTED_TUI_PLATFORMS].sort(),
    );
  });

  it("has no sidecar build for the arm64 platforms the TUI covers", () => {
    for (const p of ["linux-arm64", "win32-arm64"]) {
      expect(lock.components["tui"]![p]).toBeDefined();
      expect(lock.components["sidecar"]![p]).toBeUndefined();
    }
  });

  for (const component of COMPONENTS) {
    describe(`component: ${component}`, () => {
      const table = lock.components[component]!;
      for (const [platformKey, entry] of Object.entries(table)) {
        it(`${platformKey} entry is well-formed and self-consistent`, () => {
          const isWin = platformKey.startsWith("win32");
          expect(entry.filename).toBe(
            `${EXPECTED_FILENAME_STEM[component]}-${platformKey}${isWin ? ".exe" : ""}`,
          );
          expect(entry.executable).toBe(
            `${EXPECTED_EXECUTABLE[component]}${isWin ? ".exe" : ""}`,
          );
          // A non-Windows artifact must not carry an .exe suffix, and vice versa.
          expect(entry.filename.endsWith(".exe")).toBe(isWin);
          expect(entry.executable.endsWith(".exe")).toBe(isWin);
          expect(typeof entry.size).toBe("number");
          // Either a placeholder awaiting CI, or a real 64-char lowercase hash —
          // never something in between (a truncated or upper-case hand-edit).
          if (!isPlaceholderSha(entry.sha256)) {
            expect(entry.sha256).toMatch(/^[0-9a-f]{64}$/);
          }
        });
      }
    });
  }

  it("installs the TUI as gaia-tui, never as gaia", () => {
    // A cache-dir executable literally named `gaia` would shadow the npm bin shim.
    expect(Object.keys(pkg.bin)).toEqual(["gaia"]);
    for (const entry of Object.values(lock.components["tui"]!)) {
      expect(entry.executable.replace(/\.exe$/, "")).toBe("gaia-tui");
    }
  });

  it("gives the two components distinct executable names", () => {
    const sidecarExes = new Set(
      Object.values(lock.components["sidecar"]!).map((e) => e.executable),
    );
    const tuiExes = new Set(Object.values(lock.components["tui"]!).map((e) => e.executable));
    for (const e of sidecarExes) expect(tuiExes.has(e)).toBe(false);
  });
});
