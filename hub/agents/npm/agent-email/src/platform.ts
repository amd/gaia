// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
/**
 * Platform/arch resolution and binary-lock loading.
 *
 * The lock file (`binaries.lock.json`, shipped in the published package) is the
 * single source of truth for which artifact to download for the current host
 * and what its SHA-256 must be. Platform keys are `${process.platform}-${process.arch}`.
 */

import { fileURLToPath } from "node:url";
import path from "node:path";
import fs from "node:fs";

import { PlatformError } from "./errors.js";

/** One platform's artifact entry in the lock file. */
export interface BinaryLockEntry {
  /** Artifact filename as published under the base URL, e.g. "email-agent-win32-x64.exe". */
  filename: string;
  /** Lowercase hex SHA-256 of the downloaded artifact. */
  sha256: string;
  /** Size in bytes (informational; not enforced). */
  size?: number;
  /** Basename the executable should be written as on disk (with platform ext). */
  executable: string;
}

/** The whole lock file (`binaries.lock.json`). */
export interface BinaryLock {
  schemaVersion: string;
  agentVersion: string;
  /**
   * Default download base URL. Overridable at fetch time. May be a placeholder
   * if no binaries are published for this build.
   */
  baseUrl: string;
  binaries: Record<string, BinaryLockEntry>;
}

/** Supported platform-arch keys. */
export const SUPPORTED_PLATFORMS = [
  "win32-x64",
  "darwin-arm64",
  "darwin-x64",
  "linux-x64",
] as const;

/** Resolve the current host's platform key, e.g. "win32-x64". */
export function currentPlatformKey(
  platform: NodeJS.Platform = process.platform,
  arch: string = process.arch,
): string {
  return `${platform}-${arch}`;
}

/** Locate `binaries.lock.json` (package root, one level up from dist/ or src/). */
export function defaultLockPath(): string {
  const here = path.dirname(fileURLToPath(import.meta.url));
  // dist/platform.js -> package root is one up; in ts-source (vitest) it's also one up from src/.
  return path.resolve(here, "..", "binaries.lock.json");
}

/** Load and minimally validate the lock file. */
export function loadLock(lockPath: string = defaultLockPath()): BinaryLock {
  let raw: string;
  try {
    raw = fs.readFileSync(lockPath, "utf8");
  } catch (e) {
    throw new PlatformError(
      `cannot read binaries.lock.json at ${lockPath}: ${(e as Error).message}. ` +
        "This manifest ships with the package; reinstall @amd-gaia/agent-email if it is missing.",
    );
  }
  let parsed: BinaryLock;
  try {
    parsed = JSON.parse(raw) as BinaryLock;
  } catch (e) {
    throw new PlatformError(
      `binaries.lock.json at ${lockPath} is not valid JSON: ${(e as Error).message}`,
    );
  }
  if (!parsed.binaries || typeof parsed.binaries !== "object") {
    throw new PlatformError(
      `binaries.lock.json at ${lockPath} is missing a "binaries" map`,
    );
  }
  return parsed;
}

/** Resolve the lock entry for a platform key, failing loudly if unsupported. */
export function resolveEntry(lock: BinaryLock, platformKey: string): BinaryLockEntry {
  const entry = lock.binaries[platformKey];
  if (!entry) {
    const available = Object.keys(lock.binaries).join(", ") || "(none)";
    throw new PlatformError(
      `no email-agent binary for platform '${platformKey}'. ` +
        `Available in binaries.lock.json: ${available}. ` +
        "Supported targets: " +
        SUPPORTED_PLATFORMS.join(", "),
    );
  }
  if (!entry.sha256 || !entry.filename || !entry.executable) {
    throw new PlatformError(
      `binaries.lock.json entry for '${platformKey}' is incomplete ` +
        "(needs filename, sha256, executable) — likely a placeholder entry with " +
        "no published binary for this platform.",
    );
  }
  return entry;
}

/** True when an entry's sha256 is the not-yet-published placeholder sentinel. */
export function isPlaceholderSha(sha256: string): boolean {
  return /^0+$/.test(sha256) || sha256.toUpperCase().includes("PENDING");
}
