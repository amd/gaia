// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
/**
 * Platform/arch resolution and binary-lock loading.
 *
 * The lock file (`binaries.lock.json`, shipped in the published package) is the
 * single source of truth for which artifacts to download for the current host
 * and what their SHA-256 must be. Platform keys are
 * `${process.platform}-${process.arch}`.
 *
 * Unlike the email agent's single-binary lock, this one is keyed by COMPONENT
 * first (`sidecar` and `tui`), because `npx @amd-gaia/gaia` needs both and their
 * platform coverage differs: the Go TUI cross-compiles to arm64 Linux/Windows,
 * the frozen Python sidecar does not.
 */

import { fileURLToPath } from "node:url";
import path from "node:path";
import fs from "node:fs";

import { PlatformError } from "./errors.js";

/** The two binaries `npx @amd-gaia/gaia` needs. */
export const COMPONENTS = ["sidecar", "tui"] as const;
export type ComponentName = (typeof COMPONENTS)[number];

/** One component+platform artifact entry in the lock file. */
export interface BinaryLockEntry {
  /** Artifact filename as published under the base URL, e.g. "gaia-tui-linux-x64". */
  filename: string;
  /** Lowercase hex SHA-256 of the downloaded artifact. */
  sha256: string;
  /** Size in bytes (informational; not enforced). */
  size?: number;
  /** Basename the executable is written as on disk (with platform ext). */
  executable: string;
}

/** The whole lock file (`binaries.lock.json`), schemaVersion 2.x. */
export interface BinaryLock {
  schemaVersion: string;
  agentVersion: string;
  /** Default download base URL. Overridable at fetch time. */
  baseUrl: string;
  components: Record<string, Record<string, BinaryLockEntry>>;
}

/**
 * Platform keys the sidecar publishes for. PyInstaller freezes on the host it
 * runs on and there is no arm64 Linux/Windows CI runner for it, so this is a
 * strict subset of the TUI's coverage.
 */
export const SUPPORTED_SIDECAR_PLATFORMS = [
  "win32-x64",
  "darwin-arm64",
  "darwin-x64",
  "linux-x64",
] as const;

/** Platform keys the TUI publishes for (Go cross-compiles all six). */
export const SUPPORTED_TUI_PLATFORMS = [
  "win32-x64",
  "win32-arm64",
  "darwin-arm64",
  "darwin-x64",
  "linux-x64",
  "linux-arm64",
] as const;

/** Every platform key at least one component publishes for. */
export const SUPPORTED_PLATFORMS = SUPPORTED_TUI_PLATFORMS;

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
        "This manifest ships with the package; reinstall @amd-gaia/gaia if it is missing.",
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
  if (!parsed.components || typeof parsed.components !== "object") {
    throw new PlatformError(
      `binaries.lock.json at ${lockPath} is missing a "components" map. ` +
        'Expected schemaVersion 2.x with components.sidecar and components.tui; ' +
        `got schemaVersion '${parsed.schemaVersion ?? "(absent)"}'.`,
    );
  }
  for (const component of COMPONENTS) {
    const table = parsed.components[component];
    if (!table || typeof table !== "object") {
      throw new PlatformError(
        `binaries.lock.json at ${lockPath} has no "${component}" component. ` +
          `Both of ${COMPONENTS.join(" and ")} are required — \`gaia\` launches the ` +
          "TUI against the sidecar and cannot run with only one of them.",
      );
    }
  }
  return parsed;
}

/** The platform keys a component publishes for, per the lock. */
export function platformsFor(lock: BinaryLock, component: ComponentName): string[] {
  return Object.keys(lock.components[component] ?? {});
}

/**
 * Resolve one component's lock entry for a platform key, failing loudly when the
 * component has no build for it (e.g. the sidecar on linux-arm64).
 */
export function resolveEntry(
  lock: BinaryLock,
  component: ComponentName,
  platformKey: string,
): BinaryLockEntry {
  const table = lock.components[component];
  if (!table) {
    throw new PlatformError(
      `binaries.lock.json has no "${component}" component (components present: ` +
        `${Object.keys(lock.components).join(", ") || "(none)"}).`,
    );
  }
  const entry = table[platformKey];
  if (!entry) {
    const available = Object.keys(table).join(", ") || "(none)";
    throw new PlatformError(
      `no '${component}' binary for platform '${platformKey}'. ` +
        `Published '${component}' platforms: ${available}. ` +
        (component === "sidecar"
          ? "The frozen agent sidecar has no arm64 Linux or arm64 Windows build, so " +
            "`gaia` cannot run there yet. Run it on one of the platforms above, or " +
            "run the agent from source (see hub/agents/gaia/python/) and drive the " +
            "TUI against it."
          : "Run `gaia` on one of the platforms above."),
    );
  }
  if (!entry.sha256 || !entry.filename || !entry.executable) {
    throw new PlatformError(
      `binaries.lock.json entry for ${component}/'${platformKey}' is incomplete ` +
        "(needs filename, sha256, executable) — likely a stub entry with no " +
        "published binary for this platform.",
    );
  }
  // The lock is trusted package data, but `lockPath` is caller-overridable — a
  // name with a separator or `..` would escape the cache dir / the base URL.
  for (const [field, value] of [
    ["filename", entry.filename],
    ["executable", entry.executable],
  ] as const) {
    if (value === "." || value === ".." || /[/\\]/.test(value)) {
      throw new PlatformError(
        `binaries.lock.json entry for ${component}/'${platformKey}' has an unsafe ` +
          `${field} '${value}': it must be a bare filename with no path separators.`,
      );
    }
  }
  return entry;
}

/** True when an entry's sha256 is the not-yet-published placeholder sentinel. */
export function isPlaceholderSha(sha256: string): boolean {
  if (!sha256) return false;
  return /^0+$/.test(sha256) || sha256.toUpperCase().includes("PENDING");
}
