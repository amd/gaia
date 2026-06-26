// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
/**
 * Build-time binary fetcher.
 *
 * Resolves the current platform → looks up the artifact in `binaries.lock.json`
 * → downloads it from the lock's base URL (overridable) → **verifies its SHA-256
 * against the lock and fails loudly on any mismatch** → writes it to a resources
 * dir → `chmod +x` on POSIX.
 *
 * The SHA verify is the security boundary: a tampered or truncated download is
 * rejected before it can ever be spawned. There is NO "use it anyway" path.
 */

import crypto from "node:crypto";
import fs from "node:fs";
import fsp from "node:fs/promises";
import path from "node:path";

import { IntegrityError, PlatformError } from "./errors.js";
import { createLogger } from "./logger.js";
import { joinUrl } from "./url.js";
import {
  type BinaryLock,
  type BinaryLockEntry,
  currentPlatformKey,
  defaultLockPath,
  isPlaceholderSha,
  loadLock,
  resolveEntry,
} from "./platform.js";

const log = createLogger("fetch");

export interface FetchOptions {
  /** Directory the verified binary is written into. Required. */
  outDir: string;
  /**
   * Override the lock's `baseUrl` (e.g. to point at a local mirror). Trailing
   * slash optional.
   */
  baseUrl?: string;
  /** Override the platform key (defaults to the current host). */
  platformKey?: string;
  /** Path to the lock file (defaults to the packaged binaries.lock.json). */
  lockPath?: string;
  /** Fetch override (tests). Defaults to global `fetch`. */
  fetchImpl?: typeof fetch;
  /** Overwrite an existing verified binary. Default false (skip if hash matches). */
  force?: boolean;
  /** Abort the download after this many ms. Default 120000. Prevents a hung connection from hanging a build. */
  timeoutMs?: number;
}

export interface FetchResult {
  /** Absolute path to the written, verified executable. */
  binaryPath: string;
  /** The platform key resolved. */
  platformKey: string;
  /** The verified SHA-256 (lowercase hex). */
  sha256: string;
  /** Source URL the artifact was downloaded from. */
  url: string;
  /** True when the existing on-disk binary was reused (hash already matched). */
  cached: boolean;
}

function sha256Hex(buf: Buffer): string {
  return crypto.createHash("sha256").update(buf).digest("hex");
}

/** Compute the SHA-256 of a file on disk (used to detect a valid cache hit). */
export async function fileSha256(filePath: string): Promise<string | null> {
  try {
    const buf = await fsp.readFile(filePath);
    return sha256Hex(buf);
  } catch {
    return null;
  }
}

/**
 * Verify a buffer against an expected SHA-256. Throws `IntegrityError` loudly on
 * mismatch — this is the no-silent-fallback security gate.
 */
export function verifySha256(buf: Buffer, expected: string, sourceLabel: string): string {
  const actual = sha256Hex(buf);
  if (actual.toLowerCase() !== expected.toLowerCase()) {
    throw new IntegrityError(
      `SHA-256 mismatch for ${sourceLabel}:\n` +
        `  expected ${expected}\n` +
        `  actual   ${actual}\n` +
        "Refusing to use a binary that does not match binaries.lock.json. " +
        "The download may be corrupt, truncated, or tampered with. Re-run the fetch; " +
        "if it persists, the lock file may be stale relative to the published artifact.",
    );
  }
  return actual;
}

/**
 * Fetch + verify + install the email-agent binary for the current platform.
 *
 * @throws PlatformError   unsupported platform / incomplete lock entry / placeholder hash
 * @throws IntegrityError  SHA-256 mismatch
 * @throws Error           download/network failure (status surfaced)
 */
export async function fetchBinary(opts: FetchOptions): Promise<FetchResult> {
  if (!opts?.outDir) {
    throw new TypeError("fetchBinary requires an outDir to write the binary into");
  }
  const fetchImpl = opts.fetchImpl ?? globalThis.fetch;
  if (typeof fetchImpl !== "function") {
    throw new TypeError("global fetch unavailable — use Node >= 18 or pass fetchImpl");
  }

  const lock: BinaryLock = loadLock(opts.lockPath ?? defaultLockPath());
  const platformKey = opts.platformKey ?? currentPlatformKey();
  const entry: BinaryLockEntry = resolveEntry(lock, platformKey);
  const baseUrl = opts.baseUrl ?? lock.baseUrl;

  if (!baseUrl) {
    throw new PlatformError(
      "no download base URL: binaries.lock.json has no baseUrl and none was " +
        "passed. Pass { baseUrl } to point at where the binaries are hosted.",
    );
  }
  if (isPlaceholderSha(entry.sha256)) {
    throw new PlatformError(
      `binaries.lock.json has a placeholder sha256 for '${platformKey}' ` +
        `(${entry.sha256}), so no binary is published for it in this build. Fetch ` +
        "is blocked so a bad binary can never be trusted. To run against a locally " +
        "built binary, point the lifecycle helpers at it directly (resolveBinaryPath " +
        "/ spawnSidecar).",
    );
  }

  const outDir = path.resolve(opts.outDir);
  await fsp.mkdir(outDir, { recursive: true });
  const binaryPath = path.join(outDir, entry.executable);
  const url = joinUrl(baseUrl, entry.filename);

  // Cache hit: an already-verified binary on disk with the right hash.
  if (!opts.force) {
    const existing = await fileSha256(binaryPath);
    if (existing && existing.toLowerCase() === entry.sha256.toLowerCase()) {
      log.info(`cache hit: ${binaryPath} already matches lock sha256`);
      return { binaryPath, platformKey, sha256: existing, url, cached: true };
    }
  }

  log.info(`downloading ${platformKey} binary from ${url}`);
  const timeoutMs = opts.timeoutMs ?? 120_000;
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  let buf: Buffer;
  try {
    const res = await fetchImpl(url, {
      headers: { accept: "application/octet-stream" },
      signal: controller.signal,
    });
    if (!res.ok) {
      throw new Error(
        `download failed: HTTP ${res.status} ${res.statusText} for ${url}. ` +
          "Check the base URL and that the artifact is published for this platform.",
      );
    }
    buf = Buffer.from(await res.arrayBuffer());
  } catch (e) {
    if ((e as Error).name === "AbortError") {
      throw new Error(`download timed out after ${timeoutMs}ms for ${url}`);
    }
    throw e;
  } finally {
    clearTimeout(timer);
  }
  log.debug(`downloaded ${buf.length} bytes`);

  const sha = verifySha256(buf, entry.sha256, `${platformKey} (${url})`);

  // Write atomically-ish: write to a temp then rename so a crash mid-write
  // never leaves a half-written "verified" binary. Clean up the temp on failure.
  const tmp = `${binaryPath}.download.${process.pid}`;
  try {
    await fsp.writeFile(tmp, buf);
    await fsp.rename(tmp, binaryPath);
  } catch (e) {
    await fsp.rm(tmp, { force: true }).catch(() => undefined);
    throw e;
  }

  if (process.platform !== "win32") {
    await fsp.chmod(binaryPath, 0o755);
    log.debug(`chmod +x ${binaryPath}`);
  }

  log.info(`installed verified binary -> ${binaryPath}`);
  return { binaryPath, platformKey, sha256: sha, url, cached: false };
}

// Re-export the sync existence check for the lifecycle layer.
export function binaryExists(binaryPath: string): boolean {
  return fs.existsSync(binaryPath);
}
