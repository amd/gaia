// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
/** fetch SHA-256 verification: pass, tampered-fail, placeholder-block. */

import crypto from "node:crypto";
import fs from "node:fs";
import fsp from "node:fs/promises";
import os from "node:os";
import path from "node:path";

import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { fetchBinary, verifySha256 } from "../src/fetch.js";
import { IntegrityError, PlatformError } from "../src/errors.js";
import type { BinaryLock } from "../src/platform.js";

const PLATFORM = "linux-x64";
const ARTIFACT = Buffer.from("#!/fake-frozen-email-agent\nhello world\n");
const GOOD_SHA = crypto.createHash("sha256").update(ARTIFACT).digest("hex");

let tmp: string;

async function writeLock(sha256: string): Promise<string> {
  const lock: BinaryLock = {
    schemaVersion: "1.0",
    agentVersion: "0.1.0",
    baseUrl: "https://example.test/email-agent/0.1.0",
    binaries: {
      [PLATFORM]: {
        filename: "email-agent-linux-x64",
        executable: "email-agent",
        sha256,
        size: ARTIFACT.length,
      },
    },
  };
  const lockPath = path.join(tmp, "binaries.lock.json");
  await fsp.writeFile(lockPath, JSON.stringify(lock));
  return lockPath;
}

function fakeFetch(body: Buffer, status = 200): typeof fetch {
  return (async () =>
    new Response(status === 200 ? new Uint8Array(body) : null, {
      status,
    })) as unknown as typeof fetch;
}

beforeEach(async () => {
  tmp = await fsp.mkdtemp(path.join(os.tmpdir(), "agent-email-fetch-"));
});
afterEach(async () => {
  await fsp.rm(tmp, { recursive: true, force: true });
});

describe("verifySha256", () => {
  it("returns the hash when it matches", () => {
    expect(verifySha256(ARTIFACT, GOOD_SHA, "x")).toBe(GOOD_SHA);
  });
  it("throws IntegrityError when it does not match", () => {
    expect(() => verifySha256(ARTIFACT, "deadbeef", "x")).toThrow(IntegrityError);
  });
});

describe("fetchBinary", () => {
  it("downloads, verifies, writes, and chmods on a matching SHA", async () => {
    const lockPath = await writeLock(GOOD_SHA);
    const outDir = path.join(tmp, "resources");
    const result = await fetchBinary({
      outDir,
      platformKey: PLATFORM,
      lockPath,
      fetchImpl: fakeFetch(ARTIFACT),
    });
    expect(result.cached).toBe(false);
    expect(result.sha256).toBe(GOOD_SHA);
    expect(fs.existsSync(result.binaryPath)).toBe(true);
    expect(fs.readFileSync(result.binaryPath)).toEqual(ARTIFACT);
    // POSIX exec bit (skipped on Windows where chmod is a no-op).
    if (process.platform !== "win32") {
      const mode = fs.statSync(result.binaryPath).mode & 0o777;
      expect(mode & 0o100).toBe(0o100);
    }
  });

  it("FAILS LOUDLY (IntegrityError) when the download is tampered with", async () => {
    const lockPath = await writeLock(GOOD_SHA);
    const tampered = Buffer.concat([ARTIFACT, Buffer.from("EVIL")]);
    await expect(
      fetchBinary({
        outDir: path.join(tmp, "resources"),
        platformKey: PLATFORM,
        lockPath,
        fetchImpl: fakeFetch(tampered),
      }),
    ).rejects.toBeInstanceOf(IntegrityError);
    // The bad binary must NOT be left on disk.
    expect(fs.existsSync(path.join(tmp, "resources", "email-agent"))).toBe(false);
  });

  it("reuses a cached binary whose hash already matches", async () => {
    const lockPath = await writeLock(GOOD_SHA);
    const outDir = path.join(tmp, "resources");
    await fetchBinary({ outDir, platformKey: PLATFORM, lockPath, fetchImpl: fakeFetch(ARTIFACT) });
    // Second call with a fetch that would THROW proves the cache short-circuits.
    const result = await fetchBinary({
      outDir,
      platformKey: PLATFORM,
      lockPath,
      fetchImpl: (async () => {
        throw new Error("should not download on cache hit");
      }) as unknown as typeof fetch,
    });
    expect(result.cached).toBe(true);
  });

  it("blocks fetch when the lock has a placeholder sha (pending #1648)", async () => {
    const lockPath = await writeLock("PENDING-1648-replace-with-real-sha256");
    await expect(
      fetchBinary({
        outDir: path.join(tmp, "resources"),
        platformKey: PLATFORM,
        lockPath,
        fetchImpl: fakeFetch(ARTIFACT),
      }),
    ).rejects.toBeInstanceOf(PlatformError);
  });

  it("fails loudly on an unsupported platform", async () => {
    const lockPath = await writeLock(GOOD_SHA);
    await expect(
      fetchBinary({
        outDir: path.join(tmp, "resources"),
        platformKey: "sunos-sparc",
        lockPath,
        fetchImpl: fakeFetch(ARTIFACT),
      }),
    ).rejects.toBeInstanceOf(PlatformError);
  });

  it("surfaces a download HTTP error", async () => {
    const lockPath = await writeLock(GOOD_SHA);
    await expect(
      fetchBinary({
        outDir: path.join(tmp, "resources"),
        platformKey: PLATFORM,
        lockPath,
        fetchImpl: fakeFetch(Buffer.alloc(0), 404),
      }),
    ).rejects.toThrow(/HTTP 404/);
  });
});
