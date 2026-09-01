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
 * The lock is keyed by COMPONENT first (`sidecar` and `tui`), because
 * `npx @amd-gaia/gaia` needs both and they differ in three ways: platform
 * coverage (the Go TUI cross-compiles to arm64 Linux/Windows, the frozen Python
 * sidecar does not), version, and — since schemaVersion 3.0 — the hub lane they
 * are published in.
 *
 * The two lanes:
 *
 *   sidecar  agents/gaia/<agentVersion>/          built by this package's release
 *   tui      agents/terminal-hub/<tuiVersion>/    the `terminal-hub` component,
 *                                                 published by the core release
 *
 * The TUI is therefore the SAME binary as `gaia tui` from a core install — this
 * package consumes it rather than building a second copy, so the two can never
 * drift in behaviour or version. Each component carries its own `baseUrl`, which
 * is what schemaVersion 2.0's single top-level `baseUrl` could not express.
 *
 * The terminal-hub lane spells its Windows artifacts `win-x64` / `win-arm64`,
 * while Node's `process.platform` gives `win32`. That difference is carried by
 * the entry's `filename` (data), never by a code path — see `TUI_ARTIFACT_NAMES`.
 */

import { fileURLToPath } from "node:url";
import path from "node:path";
import fs from "node:fs";

import { PlatformError } from "./errors.js";

/** The two binaries `npx @amd-gaia/gaia` needs. */
export const COMPONENTS = ["sidecar", "tui"] as const;
export type ComponentName = (typeof COMPONENTS)[number];

/** The lock schema this package reads. Loading any other major fails loudly. */
export const SCHEMA_MAJOR = 3;

/** One component+platform artifact entry in the lock file. */
export interface BinaryLockEntry {
  /** Artifact filename as published under the component's base URL. */
  filename: string;
  /** Lowercase hex SHA-256 of the downloaded artifact. */
  sha256: string;
  /** Size in bytes (informational; not enforced). */
  size?: number;
  /** Basename the executable is written as on disk (with platform ext). */
  executable: string;
}

/** One component's lane: where it is published, at what version, for which platforms. */
export interface ComponentLock {
  /** The component's own released version — not necessarily `agentVersion`. */
  componentVersion: string;
  /** Download base URL for THIS component. Overridable at fetch time. */
  baseUrl: string;
  platforms: Record<string, BinaryLockEntry>;
}

/** The whole lock file (`binaries.lock.json`), schemaVersion 3.x. */
export interface BinaryLock {
  schemaVersion: string;
  agentVersion: string;
  components: Record<string, ComponentLock>;
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

/**
 * npm platform key → the artifact name the terminal-hub lane publishes.
 *
 * The mapping exists for exactly one reason: terminal-hub names its Windows
 * builds `win-x64` / `win-arm64` (Go's GOOS vocabulary), while our keys come
 * from `process.platform`, which says `win32`. Everything else is identical.
 * This table is the assertion, not the lookup — the lock's `filename` is what
 * the fetcher uses, and `test/lock.test.ts` checks the two agree, so a hub-side
 * rename shows up as a failing test rather than a 404 on a user's first run.
 */
export const TUI_ARTIFACT_NAMES: Record<string, string> = {
  "win32-x64": "gaia-win-x64.exe",
  "win32-arm64": "gaia-win-arm64.exe",
  "darwin-arm64": "gaia-darwin-arm64",
  "darwin-x64": "gaia-darwin-x64",
  "linux-x64": "gaia-linux-x64",
  "linux-arm64": "gaia-linux-arm64",
};

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
  const major = Number.parseInt(String(parsed.schemaVersion ?? ""), 10);
  if (major !== SCHEMA_MAJOR) {
    throw new PlatformError(
      `binaries.lock.json at ${lockPath} declares schemaVersion ` +
        `'${parsed.schemaVersion ?? "(absent)"}', but this package reads ` +
        `${SCHEMA_MAJOR}.x — where each component carries its own baseUrl ` +
        "(the sidecar from the gaia lane, the TUI from the terminal-hub lane). " +
        "Reinstall @amd-gaia/gaia so the lock and the code ship together.",
    );
  }
  if (!parsed.components || typeof parsed.components !== "object") {
    throw new PlatformError(
      `binaries.lock.json at ${lockPath} is missing a "components" map. ` +
        `Expected schemaVersion ${SCHEMA_MAJOR}.x with components.sidecar and components.tui.`,
    );
  }
  for (const component of COMPONENTS) {
    const lane = parsed.components[component];
    if (!lane || typeof lane !== "object") {
      throw new PlatformError(
        `binaries.lock.json at ${lockPath} has no "${component}" component. ` +
          `Both of ${COMPONENTS.join(" and ")} are required — \`gaia\` launches the ` +
          "TUI against the sidecar and cannot run with only one of them.",
      );
    }
    if (!lane.platforms || typeof lane.platforms !== "object") {
      throw new PlatformError(
        `binaries.lock.json at ${lockPath}: component "${component}" has no ` +
          `"platforms" map. Under schemaVersion ${SCHEMA_MAJOR}.x each component is ` +
          '{ componentVersion, baseUrl, platforms }, not a bare platform map.',
      );
    }
    if (typeof lane.baseUrl !== "string" || lane.baseUrl === "") {
      throw new PlatformError(
        `binaries.lock.json at ${lockPath}: component "${component}" has no ` +
          '"baseUrl". Each component is published in its own hub lane at its own ' +
          "version, so each carries its own download base URL.",
      );
    }
  }
  return parsed;
}

/** One component's lane, failing loudly when the lock does not declare it. */
export function componentLock(lock: BinaryLock, component: ComponentName): ComponentLock {
  const lane = lock.components[component];
  if (!lane) {
    throw new PlatformError(
      `binaries.lock.json has no "${component}" component (components present: ` +
        `${Object.keys(lock.components).join(", ") || "(none)"}).`,
    );
  }
  return lane;
}

/** The download base URL for one component. */
export function componentBaseUrl(lock: BinaryLock, component: ComponentName): string {
  const { baseUrl } = componentLock(lock, component);
  if (!baseUrl) {
    throw new PlatformError(
      `binaries.lock.json has no baseUrl for "${component}", so there is nowhere ` +
        "to download it from. Pass { baseUrl } to point at where the binaries are hosted.",
    );
  }
  return baseUrl;
}

/** The platform keys a component publishes for, per the lock. */
export function platformsFor(lock: BinaryLock, component: ComponentName): string[] {
  return Object.keys(lock.components[component]?.platforms ?? {});
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
  const table = componentLock(lock, component).platforms;
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
