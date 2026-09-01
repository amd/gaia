// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
/** Platform-key resolution and per-component lock lookup. */

import fsp from "node:fs/promises";
import os from "node:os";
import path from "node:path";

import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { PlatformError } from "../src/errors.js";
import {
  COMPONENTS,
  SUPPORTED_SIDECAR_PLATFORMS,
  SUPPORTED_TUI_PLATFORMS,
  componentBaseUrl,
  currentPlatformKey,
  isPlaceholderSha,
  loadLock,
  platformsFor,
  resolveEntry,
} from "../src/platform.js";
import { SIDECAR_BASE, TUI_BASE, makeLock, writeLockFile } from "./helpers.js";

let tmp: string;
beforeEach(async () => {
  tmp = await fsp.mkdtemp(path.join(os.tmpdir(), "gaia-platform-"));
});
afterEach(async () => {
  await fsp.rm(tmp, { recursive: true, force: true });
});

describe("currentPlatformKey", () => {
  it("joins platform and arch", () => {
    expect(currentPlatformKey("linux", "x64")).toBe("linux-x64");
    expect(currentPlatformKey("win32", "arm64")).toBe("win32-arm64");
    expect(currentPlatformKey("darwin", "arm64")).toBe("darwin-arm64");
  });

  it("defaults to this host", () => {
    expect(currentPlatformKey()).toBe(`${process.platform}-${process.arch}`);
  });
});

describe("isPlaceholderSha", () => {
  it("recognises the PENDING sentinel and an all-zero hash", () => {
    expect(isPlaceholderSha("PENDING-replace-with-real-sha256")).toBe(true);
    expect(isPlaceholderSha("pending")).toBe(true);
    expect(isPlaceholderSha("0".repeat(64))).toBe(true);
  });

  it("accepts a real hash", () => {
    expect(isPlaceholderSha("a".repeat(64))).toBe(false);
  });
});

describe("loadLock", () => {
  it("rejects a v1-shaped lock with no components map", async () => {
    const p = path.join(tmp, "old.json");
    await fsp.writeFile(
      p,
      JSON.stringify({ schemaVersion: "1.0", binaries: { "linux-x64": {} } }),
    );
    expect(() => loadLock(p)).toThrow(PlatformError);
    expect(() => loadLock(p)).toThrow(/schemaVersion/);
  });

  it("rejects the 2.0 shape (shared baseUrl, bare platform maps)", async () => {
    // 2.0 put one baseUrl at the top and hung platforms directly off each
    // component. Reading it as 3.0 would silently look for a baseUrl that is not
    // there — so it must be refused by version, before any field access.
    const p = path.join(tmp, "v2.json");
    await fsp.writeFile(
      p,
      JSON.stringify({
        schemaVersion: "2.0",
        agentVersion: "0.1.0",
        baseUrl: "https://example.test/agents/gaia/0.1.0",
        components: { sidecar: { "linux-x64": {} }, tui: { "linux-x64": {} } },
      }),
    );
    expect(() => loadLock(p)).toThrow(PlatformError);
    expect(() => loadLock(p)).toThrow(/schemaVersion '2\.0'/);
  });

  it("rejects a component with no baseUrl of its own", async () => {
    const lock = makeLock();
    delete (lock.components["tui"] as unknown as Record<string, unknown>)["baseUrl"];
    const p = await writeLockFile(tmp, lock);
    expect(() => loadLock(p)).toThrow(/"tui" has no\s+"baseUrl"|no\s+"baseUrl"/);
  });

  it("rejects a component with no platforms map", async () => {
    const lock = makeLock();
    delete (lock.components["sidecar"] as unknown as Record<string, unknown>)["platforms"];
    const p = await writeLockFile(tmp, lock);
    expect(() => loadLock(p)).toThrow(/"platforms"/);
  });

  it("rejects a lock missing one of the two components", async () => {
    const lock = makeLock();
    delete (lock.components as Record<string, unknown>)["tui"];
    const p = await writeLockFile(tmp, lock);
    expect(() => loadLock(p)).toThrow(/no "tui" component/);
  });

  it("fails loudly on unreadable and on malformed JSON", async () => {
    expect(() => loadLock(path.join(tmp, "nope.json"))).toThrow(PlatformError);
    const bad = path.join(tmp, "bad.json");
    await fsp.writeFile(bad, "{not json");
    expect(() => loadLock(bad)).toThrow(/not valid JSON/);
  });
});

describe("componentBaseUrl", () => {
  it("gives each component its own lane", () => {
    const lock = makeLock();
    expect(componentBaseUrl(lock, "sidecar")).toBe(SIDECAR_BASE);
    expect(componentBaseUrl(lock, "tui")).toBe(TUI_BASE);
  });

  it("fails loudly for a component the lock does not declare", () => {
    const lock = makeLock();
    delete (lock.components as Record<string, unknown>)["tui"];
    expect(() => componentBaseUrl(lock, "tui")).toThrow(PlatformError);
  });
});

describe("resolveEntry", () => {
  it("resolves each component independently", () => {
    const lock = makeLock();
    expect(resolveEntry(lock, "sidecar", "linux-x64").executable).toBe("gaia-agent");
    expect(resolveEntry(lock, "tui", "linux-x64").executable).toBe("gaia-tui");
  });

  it("maps the win32 platform key onto terminal-hub's win-* artifact name", () => {
    const lock = makeLock();
    const entry = resolveEntry(lock, "tui", "win32-x64");
    expect(entry.filename).toBe("gaia-win-x64.exe");
    expect(entry.executable).toBe("gaia-tui.exe");
    // Our own lane keeps the process.platform spelling — only the TUI crosses over.
    expect(resolveEntry(lock, "sidecar", "win32-x64").filename).toBe(
      "gaia-agent-win32-x64.exe",
    );
  });

  it("throws for an unsupported platform on both components", () => {
    const lock = makeLock();
    for (const c of COMPONENTS) {
      expect(() => resolveEntry(lock, c, "sunos-sparc")).toThrow(PlatformError);
    }
  });

  it("throws with an actionable message when the SIDECAR has no arm64 Linux build", () => {
    const lock = makeLock();
    // The TUI publishes for it; the frozen sidecar does not. That asymmetry is
    // the whole reason the lock is component-keyed, so it must fail loudly.
    expect(() => resolveEntry(lock, "tui", "linux-arm64")).not.toThrow();
    expect(() => resolveEntry(lock, "sidecar", "linux-arm64")).toThrow(PlatformError);
    try {
      resolveEntry(lock, "sidecar", "linux-arm64");
    } catch (e) {
      const msg = (e as Error).message;
      expect(msg).toContain("linux-arm64"); // names the platform
      expect(msg).toContain("win32-x64"); // names the supported set
      expect(msg).toContain("darwin-arm64");
    }
  });

  it("throws for the sidecar on arm64 Windows too", () => {
    const lock = makeLock();
    expect(() => resolveEntry(lock, "tui", "win32-arm64")).not.toThrow();
    expect(() => resolveEntry(lock, "sidecar", "win32-arm64")).toThrow(PlatformError);
  });

  it("throws on an incomplete entry", () => {
    const lock = makeLock();
    lock.components["sidecar"]!.platforms["linux-x64"] = {
      filename: "",
      executable: "gaia-agent",
      sha256: "abc",
    };
    expect(() => resolveEntry(lock, "sidecar", "linux-x64")).toThrow(/incomplete/);
  });

  it("rejects a filename that would escape the cache dir or the base URL", () => {
    const lock = makeLock();
    lock.components["tui"]!.platforms["linux-x64"] = {
      filename: "../../etc/passwd",
      executable: "gaia-tui",
      sha256: "c".repeat(64),
    };
    expect(() => resolveEntry(lock, "tui", "linux-x64")).toThrow(/unsafe filename/);
  });
});

describe("declared platform support", () => {
  it("the sidecar's supported set is a strict subset of the TUI's", () => {
    for (const p of SUPPORTED_SIDECAR_PLATFORMS) {
      expect(SUPPORTED_TUI_PLATFORMS as readonly string[]).toContain(p);
    }
    expect(SUPPORTED_TUI_PLATFORMS.length).toBeGreaterThan(
      SUPPORTED_SIDECAR_PLATFORMS.length,
    );
  });

  it("platformsFor reports each component's own coverage", () => {
    const lock = makeLock();
    expect(platformsFor(lock, "sidecar").sort()).toEqual(
      [...SUPPORTED_SIDECAR_PLATFORMS].sort(),
    );
    expect(platformsFor(lock, "tui").sort()).toEqual([...SUPPORTED_TUI_PLATFORMS].sort());
  });
});
