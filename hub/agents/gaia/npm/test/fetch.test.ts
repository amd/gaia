// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
/**
 * The integrity gate: SHA-256 verify passes, tampered downloads fail, placeholder
 * hashes are blocked, and the outgoing request is the URL we claim it is.
 *
 * Mocks prove "we called it", not "the call is valid" (CLAUDE.md) — so the fake
 * fetch asserts the *shape* of the request (exact URL = baseUrl + "/" + filename),
 * not merely that something was fetched.
 */

import crypto from "node:crypto";
import fs from "node:fs";
import fsp from "node:fs/promises";
import os from "node:os";
import path from "node:path";

import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { IntegrityError, PlatformError } from "../src/errors.js";
import { fetchAll, fetchBinary, verifySha256 } from "../src/fetch.js";
import { makeLock, writeLockFile } from "./helpers.js";

const SIDECAR_BYTES = Buffer.from("#!/fake-frozen-gaia-agent\n");
const TUI_BYTES = Buffer.from("#!/fake-gaia-tui\n");
const sha = (b: Buffer): string => crypto.createHash("sha256").update(b).digest("hex");
const SIDECAR_SHA = sha(SIDECAR_BYTES);
const TUI_SHA = sha(TUI_BYTES);

let tmp: string;
const urls: string[] = [];

/** A fetch stub that records every requested URL and serves per-artifact bytes. */
function recordingFetch(
  bodies: Record<string, Buffer>,
  status = 200,
): typeof fetch {
  return (async (url: string) => {
    urls.push(String(url));
    const name = String(url).split("/").pop() ?? "";
    const body = bodies[name];
    if (status !== 200 || body === undefined) {
      return new Response(null, { status: status === 200 ? 404 : status });
    }
    return new Response(new Uint8Array(body), { status: 200 });
  }) as unknown as typeof fetch;
}

/** A lock whose per-component SHAs are the real hashes of the fake artifacts. */
async function realShaLock(): Promise<string> {
  const lock = makeLock();
  for (const e of Object.values(lock.components["sidecar"]!)) e.sha256 = SIDECAR_SHA;
  for (const e of Object.values(lock.components["tui"]!)) e.sha256 = TUI_SHA;
  return writeLockFile(tmp, lock);
}

const BODIES = {
  "gaia-agent-linux-x64": SIDECAR_BYTES,
  "gaia-tui-linux-x64": TUI_BYTES,
};

beforeEach(async () => {
  tmp = await fsp.mkdtemp(path.join(os.tmpdir(), "gaia-fetch-"));
  urls.length = 0;
});
afterEach(async () => {
  await fsp.rm(tmp, { recursive: true, force: true });
});

describe("verifySha256", () => {
  it("returns the hash when it matches", () => {
    expect(verifySha256(SIDECAR_BYTES, SIDECAR_SHA, "x")).toBe(SIDECAR_SHA);
  });

  it("is case-insensitive on the expected hash", () => {
    expect(verifySha256(SIDECAR_BYTES, SIDECAR_SHA.toUpperCase(), "x")).toBe(SIDECAR_SHA);
  });

  it("throws IntegrityError naming expected vs actual when it does not match", () => {
    try {
      verifySha256(SIDECAR_BYTES, "deadbeef", "sidecar linux-x64");
      throw new Error("should have thrown");
    } catch (e) {
      expect(e).toBeInstanceOf(IntegrityError);
      expect((e as Error).message).toContain("deadbeef");
      expect((e as Error).message).toContain(SIDECAR_SHA);
    }
  });
});

describe("fetchBinary", () => {
  it("downloads, verifies, writes and (POSIX) chmods the sidecar", async () => {
    const lockPath = await realShaLock();
    const outDir = path.join(tmp, "sidecar");
    const res = await fetchBinary({
      component: "sidecar",
      outDir,
      platformKey: "linux-x64",
      lockPath,
      fetchImpl: recordingFetch(BODIES),
    });
    expect(res.cached).toBe(false);
    expect(res.sha256).toBe(SIDECAR_SHA);
    expect(res.binaryPath).toBe(path.join(outDir, "gaia-agent"));
    expect(fs.readFileSync(res.binaryPath)).toEqual(SIDECAR_BYTES);
    if (process.platform !== "win32") {
      expect(fs.statSync(res.binaryPath).mode & 0o100).toBe(0o100);
    }
  });

  it("requests exactly baseUrl + '/' + filename", async () => {
    const lockPath = await realShaLock();
    await fetchBinary({
      component: "tui",
      outDir: path.join(tmp, "tui"),
      platformKey: "linux-x64",
      lockPath,
      fetchImpl: recordingFetch(BODIES),
    });
    // The call SHAPE, not just that fetch happened: a wrong join (double slash,
    // missing segment, wrong artifact name) is a 404 against the real hub.
    expect(urls).toEqual(["https://example.test/agents/gaia/0.1.0/gaia-tui-linux-x64"]);
  });

  it("honours a --base-url override with a trailing slash without doubling it", async () => {
    const lockPath = await realShaLock();
    await fetchBinary({
      component: "tui",
      outDir: path.join(tmp, "tui"),
      platformKey: "linux-x64",
      lockPath,
      baseUrl: "https://mirror.test/gaia/0.1.0/",
      fetchImpl: recordingFetch(BODIES),
    });
    expect(urls).toEqual(["https://mirror.test/gaia/0.1.0/gaia-tui-linux-x64"]);
  });

  it("names the win32 artifact with its .exe extension", async () => {
    const lockPath = await realShaLock();
    const bodies = { "gaia-tui-win32-x64.exe": TUI_BYTES };
    const res = await fetchBinary({
      component: "tui",
      outDir: path.join(tmp, "tui"),
      platformKey: "win32-x64",
      lockPath,
      fetchImpl: recordingFetch(bodies),
    });
    expect(urls).toEqual([
      "https://example.test/agents/gaia/0.1.0/gaia-tui-win32-x64.exe",
    ]);
    expect(path.basename(res.binaryPath)).toBe("gaia-tui.exe");
  });

  it("FAILS LOUDLY (IntegrityError) on a tampered download and leaves nothing behind", async () => {
    const lockPath = await realShaLock();
    const outDir = path.join(tmp, "sidecar");
    const tampered = Buffer.concat([SIDECAR_BYTES, Buffer.from("EVIL")]);
    await expect(
      fetchBinary({
        component: "sidecar",
        outDir,
        platformKey: "linux-x64",
        lockPath,
        fetchImpl: recordingFetch({ "gaia-agent-linux-x64": tampered }),
      }),
    ).rejects.toBeInstanceOf(IntegrityError);
    expect(fs.existsSync(path.join(outDir, "gaia-agent"))).toBe(false);
  });

  it("BLOCKS the fetch when the lock still holds a placeholder hash", async () => {
    // This is the shipped state of binaries.lock.json until CI regenerates it.
    const lockPath = await writeLockFile(tmp, makeLock("PENDING-replace-with-real-sha256"));
    for (const component of ["sidecar", "tui"] as const) {
      const p = fetchBinary({
        component,
        outDir: path.join(tmp, component),
        platformKey: "linux-x64",
        lockPath,
        fetchImpl: recordingFetch(BODIES),
      });
      await expect(p).rejects.toBeInstanceOf(PlatformError);
      await expect(p).rejects.toThrow(/placeholder sha256/);
    }
    // Blocked BEFORE any network call — an unverifiable artifact is never fetched.
    expect(urls).toEqual([]);
  });

  it("blocks an all-zero placeholder hash too", async () => {
    const lockPath = await writeLockFile(tmp, makeLock("0".repeat(64)));
    await expect(
      fetchBinary({
        component: "tui",
        outDir: path.join(tmp, "tui"),
        platformKey: "linux-x64",
        lockPath,
        fetchImpl: recordingFetch(BODIES),
      }),
    ).rejects.toBeInstanceOf(PlatformError);
  });

  it("fails loudly when the SIDECAR has no build for an arm64 Linux host", async () => {
    const lockPath = await realShaLock();
    // The TUI resolves on linux-arm64 ...
    await expect(
      fetchBinary({
        component: "tui",
        outDir: path.join(tmp, "tui"),
        platformKey: "linux-arm64",
        lockPath,
        fetchImpl: recordingFetch({ "gaia-tui-linux-arm64": TUI_BYTES }),
      }),
    ).resolves.toMatchObject({ platformKey: "linux-arm64" });
    // ... the sidecar does not, and must say so by name.
    const p = fetchBinary({
      component: "sidecar",
      outDir: path.join(tmp, "sidecar"),
      platformKey: "linux-arm64",
      lockPath,
      fetchImpl: recordingFetch(BODIES),
    });
    await expect(p).rejects.toBeInstanceOf(PlatformError);
    await expect(p).rejects.toThrow(/linux-arm64/);
  });

  it("reuses a cached binary whose hash already matches", async () => {
    const lockPath = await realShaLock();
    const outDir = path.join(tmp, "sidecar");
    await fetchBinary({
      component: "sidecar",
      outDir,
      platformKey: "linux-x64",
      lockPath,
      fetchImpl: recordingFetch(BODIES),
    });
    // A fetch that would THROW proves the cache short-circuits before the network.
    const res = await fetchBinary({
      component: "sidecar",
      outDir,
      platformKey: "linux-x64",
      lockPath,
      fetchImpl: (async () => {
        throw new Error("should not download on cache hit");
      }) as unknown as typeof fetch,
    });
    expect(res.cached).toBe(true);
  });

  it("re-downloads a cached binary whose bytes no longer match the lock", async () => {
    const lockPath = await realShaLock();
    const outDir = path.join(tmp, "sidecar");
    await fsp.mkdir(outDir, { recursive: true });
    await fsp.writeFile(path.join(outDir, "gaia-agent"), "stale contents");
    const res = await fetchBinary({
      component: "sidecar",
      outDir,
      platformKey: "linux-x64",
      lockPath,
      fetchImpl: recordingFetch(BODIES),
    });
    expect(res.cached).toBe(false);
    expect(fs.readFileSync(res.binaryPath)).toEqual(SIDECAR_BYTES);
  });

  it("surfaces a download HTTP error", async () => {
    const lockPath = await realShaLock();
    await expect(
      fetchBinary({
        component: "tui",
        outDir: path.join(tmp, "tui"),
        platformKey: "linux-x64",
        lockPath,
        fetchImpl: recordingFetch(BODIES, 503),
      }),
    ).rejects.toThrow(/HTTP 503/);
  });

  it("rejects an unknown component at the type boundary", async () => {
    const lockPath = await realShaLock();
    await expect(
      fetchBinary({
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        component: "installer" as any,
        outDir: path.join(tmp, "x"),
        platformKey: "linux-x64",
        lockPath,
        fetchImpl: recordingFetch(BODIES),
      }),
    ).rejects.toBeInstanceOf(PlatformError);
  });
});

describe("fetchAll", () => {
  it("fetches BOTH components and verifies each against its own hash", async () => {
    const lockPath = await realShaLock();
    const { sidecar, tui } = await fetchAll({
      lockPath,
      platformKey: "linux-x64",
      sidecarDir: path.join(tmp, "agents", "gaia"),
      tuiDir: path.join(tmp, "cache"),
      fetchImpl: recordingFetch(BODIES),
    });
    expect(sidecar.sha256).toBe(SIDECAR_SHA);
    expect(tui.sha256).toBe(TUI_SHA);
    expect(urls).toEqual([
      "https://example.test/agents/gaia/0.1.0/gaia-agent-linux-x64",
      "https://example.test/agents/gaia/0.1.0/gaia-tui-linux-x64",
    ]);
    expect(fs.existsSync(sidecar.binaryPath)).toBe(true);
    expect(fs.existsSync(tui.binaryPath)).toBe(true);
  });

  it("aborts on the FIRST integrity failure and never installs the second binary", async () => {
    const lockPath = await realShaLock();
    const tuiDir = path.join(tmp, "cache");
    await expect(
      fetchAll({
        lockPath,
        platformKey: "linux-x64",
        sidecarDir: path.join(tmp, "agents", "gaia"),
        tuiDir,
        fetchImpl: recordingFetch({
          "gaia-agent-linux-x64": Buffer.from("tampered"),
          "gaia-tui-linux-x64": TUI_BYTES,
        }),
      }),
    ).rejects.toBeInstanceOf(IntegrityError);
    expect(fs.existsSync(path.join(tuiDir, "gaia-tui"))).toBe(false);
  });
});
