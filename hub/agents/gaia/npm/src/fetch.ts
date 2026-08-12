// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
/**
 * Binary fetcher for both components (`sidecar` and `tui`).
 *
 * Resolves the current platform → looks the component up in
 * `binaries.lock.json` → downloads it from THAT COMPONENT's base URL
 * (overridable) → **verifies its SHA-256 against the lock and fails loudly on
 * any mismatch** → writes it into a cache dir → `chmod +x` on POSIX.
 *
 * The two components come from different hub lanes: the sidecar from
 * `agents/gaia/<agentVersion>/`, the TUI from `agents/terminal-hub/<tuiVersion>/`
 * (the same binary a core install runs as `gaia tui`). Each entry's base URL
 * comes from its own component, never from a shared top-level one.
 *
 * The SHA verify is the security boundary: a tampered, truncated, or
 * not-yet-published artifact is rejected before it can ever be spawned. There is
 * NO "use it anyway" path, and a placeholder hash in the lock blocks the fetch
 * outright rather than degrading to an unverified download.
 */

import crypto from "node:crypto";
import fs from "node:fs";
import fsp from "node:fs/promises";
import os from "node:os";
import path from "node:path";

import { IntegrityError, PlatformError } from "./errors.js";
import { createLogger } from "./logger.js";
import { joinUrl } from "./url.js";
import {
  type BinaryLock,
  type BinaryLockEntry,
  type ComponentName,
  componentBaseUrl,
  currentPlatformKey,
  defaultLockPath,
  isPlaceholderSha,
  loadLock,
  resolveEntry,
} from "./platform.js";

const log = createLogger("fetch");

/**
 * Where verified binaries are cached, keyed by agent version so a version bump
 * never reuses the previous release's executables.
 */
export function defaultCacheDir(agentVersion: string): string {
  return path.join(os.homedir(), ".gaia", "npm-cache", `gaia-${agentVersion}`);
}

/**
 * The daemon's own sidecar cache — `~/.gaia/agents/gaia/`, mirroring
 * `gaia.daemon.sidecars.fetch.default_cache_dir("gaia")`. Staging the verified
 * sidecar here means the daemon's own fetch is a SHA-256 cache hit instead of a
 * second download. This path is a cross-repo contract with the daemon.
 */
export function daemonSidecarCacheDir(agentId = "gaia"): string {
  return path.join(os.homedir(), ".gaia", "agents", agentId);
}

export interface FetchOptions {
  /** Which binary to fetch. */
  component: ComponentName;
  /** Directory the verified binary is written into. Required. */
  outDir: string;
  /**
   * Override this component's `baseUrl` (e.g. a local mirror). Trailing slash
   * optional. Applies to whichever component is being fetched — the two lanes'
   * filenames never collide, so one flat mirror directory can serve both.
   */
  baseUrl?: string;
  /** Override the platform key (defaults to the current host). */
  platformKey?: string;
  /** Path to the lock file (defaults to the packaged binaries.lock.json). */
  lockPath?: string;
  /** A pre-loaded lock, to avoid re-reading it per component. */
  lock?: BinaryLock;
  /** Fetch override (tests). Defaults to global `fetch`. */
  fetchImpl?: typeof fetch;
  /** Re-download even when a verified binary is already cached. Default false. */
  force?: boolean;
  /** Abort the download after this many ms. Default 300000 (the sidecar is ~200MB). */
  timeoutMs?: number;
}

export interface FetchResult {
  component: ComponentName;
  /** Absolute path to the written, verified executable. */
  binaryPath: string;
  /** The platform key resolved. */
  platformKey: string;
  /** The verified SHA-256 (lowercase hex). */
  sha256: string;
  /** Source URL the artifact was downloaded from. */
  url: string;
  /** True when the on-disk binary was reused (hash already matched). */
  cached: boolean;
}

function sha256Hex(buf: Buffer): string {
  return crypto.createHash("sha256").update(buf).digest("hex");
}

/**
 * SHA-256 of a file on disk, or null when it is simply absent. Any other error
 * (a permission problem, a directory in the way) is re-raised: reading it as
 * "no cache" would trigger a re-download that then fails on write with a far
 * less useful message.
 */
export async function fileSha256(filePath: string): Promise<string | null> {
  try {
    return sha256Hex(await fsp.readFile(filePath));
  } catch (e) {
    const code = (e as NodeJS.ErrnoException).code;
    if (code === "ENOENT") return null;
    throw new Error(
      `cannot read the cached binary at ${filePath} to check its hash: ${(e as Error).message}. ` +
        "Fix the permissions on that path, or pass a different --cache-dir / --sidecar-dir.",
      { cause: e },
    );
  }
}

/**
 * Verify a buffer against an expected SHA-256. Throws `IntegrityError` loudly on
 * mismatch — this is the no-silent-fallback integrity gate.
 */
export function verifySha256(
  buf: Buffer,
  expected: string,
  sourceLabel: string,
): string {
  const actual = sha256Hex(buf);
  if (actual.toLowerCase() !== expected.toLowerCase()) {
    throw new IntegrityError(
      `SHA-256 mismatch for ${sourceLabel}:\n` +
        `  expected ${expected}\n` +
        `  actual   ${actual}\n` +
        "Refusing to use a binary that does not match binaries.lock.json. " +
        "The download may be corrupt, truncated, or tampered with. Re-run the fetch; " +
        "if it persists, the lock is stale relative to the published artifact — " +
        "report it at https://github.com/amd/gaia/issues.",
    );
  }
  return actual;
}

/**
 * Fetch + verify + install one component's binary for the current platform.
 *
 * @throws PlatformError   unsupported platform / incomplete entry / placeholder hash
 * @throws IntegrityError  SHA-256 mismatch
 * @throws Error           download/network failure (HTTP status surfaced)
 */
export async function fetchBinary(opts: FetchOptions): Promise<FetchResult> {
  if (!opts?.outDir) {
    throw new TypeError("fetchBinary requires an outDir to write the binary into");
  }
  if (!opts?.component) {
    throw new TypeError('fetchBinary requires a component ("sidecar" or "tui")');
  }
  const fetchImpl = opts.fetchImpl ?? globalThis.fetch;
  if (typeof fetchImpl !== "function") {
    throw new TypeError("global fetch unavailable — use Node >= 18 or pass fetchImpl");
  }

  const lock: BinaryLock = opts.lock ?? loadLock(opts.lockPath ?? defaultLockPath());
  const platformKey = opts.platformKey ?? currentPlatformKey();
  const entry: BinaryLockEntry = resolveEntry(lock, opts.component, platformKey);
  // Per-component: the sidecar and the TUI live in different hub lanes at
  // different versions, so there is no single base URL to fall back to.
  const baseUrl = opts.baseUrl ?? componentBaseUrl(lock, opts.component);

  if (isPlaceholderSha(entry.sha256)) {
    throw new PlatformError(
      `binaries.lock.json has a placeholder sha256 for ${opts.component}/'${platformKey}' ` +
        `(${entry.sha256}), so no verifiable binary is published for it in this build. ` +
        "Fetch is blocked so an unverifiable binary can never be trusted. " +
        "Install a released @amd-gaia/gaia, or build the binary locally " +
        "(hub/agents/gaia/python/packaging for the sidecar, `make -C tui cross-compile` " +
        "for the TUI) and point the lifecycle helpers at it directly.",
    );
  }

  const outDir = path.resolve(opts.outDir);
  await fsp.mkdir(outDir, { recursive: true });
  const binaryPath = path.join(outDir, entry.executable);
  const url = joinUrl(baseUrl, entry.filename);

  if (!opts.force) {
    const existing = await fileSha256(binaryPath);
    if (existing && existing.toLowerCase() === entry.sha256.toLowerCase()) {
      log.debug(`cache hit: ${binaryPath} already matches lock sha256`);
      // Re-apply the exec bit: an interrupted earlier run or a restrictive umask
      // can leave correct bytes that still cannot be spawned.
      if (process.platform !== "win32") await fsp.chmod(binaryPath, 0o755);
      return {
        component: opts.component,
        binaryPath,
        platformKey,
        sha256: existing,
        url,
        cached: true,
      };
    }
  }

  log.info(`downloading ${opts.component} for ${platformKey} from ${url}`);
  const timeoutMs = opts.timeoutMs ?? 300_000;
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

  const sha = verifySha256(buf, entry.sha256, `${opts.component} ${platformKey} (${url})`);

  // Write to a temp then rename, so a crash mid-write never leaves a
  // half-written file that a later run would treat as a real binary.
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
  }

  log.info(`installed verified ${opts.component} -> ${binaryPath}`);
  return {
    component: opts.component,
    binaryPath,
    platformKey,
    sha256: sha,
    url,
    cached: false,
  };
}

export interface FetchAllOptions extends Omit<FetchOptions, "component" | "outDir"> {
  /** Cache dir for the TUI. Defaults to `defaultCacheDir(lock.agentVersion)`. */
  tuiDir?: string;
  /** Cache dir for the sidecar. Defaults to the daemon's `~/.gaia/agents/gaia`. */
  sidecarDir?: string;
}

export interface FetchAllResult {
  sidecar: FetchResult;
  tui: FetchResult;
  lock: BinaryLock;
}

/**
 * Fetch + verify BOTH binaries. Sequential on purpose: the sidecar is the large
 * download and a failure there should not race a half-finished TUI download.
 *
 * The sidecar lands in the daemon's own cache dir so the daemon — which owns
 * spawning it — finds it already verified instead of downloading it again.
 */
export async function fetchAll(opts: FetchAllOptions = {}): Promise<FetchAllResult> {
  const lock = opts.lock ?? loadLock(opts.lockPath ?? defaultLockPath());
  const platformKey = opts.platformKey ?? currentPlatformKey();
  const common = { ...opts, lock, platformKey };
  const sidecar = await fetchBinary({
    ...common,
    component: "sidecar",
    outDir: opts.sidecarDir ?? daemonSidecarCacheDir(),
  });
  const tui = await fetchBinary({
    ...common,
    component: "tui",
    outDir: opts.tuiDir ?? defaultCacheDir(lock.agentVersion),
  });
  return { sidecar, tui, lock };
}

/** Sync existence check, for the lifecycle layer. */
export function binaryExists(binaryPath: string): boolean {
  return fs.existsSync(binaryPath);
}
